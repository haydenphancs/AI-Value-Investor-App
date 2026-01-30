//
//  FullScreenAudioPlayer.swift
//  ios
//
//  Full screen audio player with complete playback controls
//  Expanded from mini player, dismissible via swipe down gesture
//

import SwiftUI

struct FullScreenAudioPlayer: View {
    @EnvironmentObject private var audioManager: AudioManager
    @State private var dragOffset: CGFloat = 0
    @State private var showSpeedPicker: Bool = false
    @State private var showSleepTimer: Bool = false
    @State private var showQueue: Bool = false

    private let dismissThreshold: CGFloat = 150

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // Background gradient
                backgroundGradient
                    .ignoresSafeArea()

                // Main content
                VStack(spacing: 0) {
                    // Drag indicator and header
                    headerSection

                    Spacer()

                    // Artwork
                    artworkSection

                    Spacer()

                    // Title and info
                    titleSection

                    // Progress bar
                    progressSection
                        .padding(.top, AppSpacing.xxl)

                    // Main controls
                    controlsSection
                        .padding(.top, AppSpacing.xl)

                    // Secondary controls
                    secondaryControlsSection
                        .padding(.top, AppSpacing.xxl)

                    Spacer()
                        .frame(height: geometry.safeAreaInsets.bottom + AppSpacing.xl)
                }
                .padding(.horizontal, AppSpacing.xl)
            }
            .offset(y: dragOffset)
            .gesture(
                DragGesture()
                    .onChanged { value in
                        // Only allow downward drag
                        if value.translation.height > 0 {
                            dragOffset = value.translation.height
                        }
                    }
                    .onEnded { value in
                        if value.translation.height > dismissThreshold ||
                            value.predictedEndTranslation.height > dismissThreshold * 2 {
                            audioManager.collapsePlayer()
                        }
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                            dragOffset = 0
                        }
                    }
            )
        }
        .sheet(isPresented: $showSpeedPicker) {
            PlaybackSpeedSheet()
                .environmentObject(audioManager)
                .presentationDetents([.height(320)])
        }
        .sheet(isPresented: $showSleepTimer) {
            SleepTimerSheet()
                .environmentObject(audioManager)
                .presentationDetents([.height(400)])
        }
        .sheet(isPresented: $showQueue) {
            AudioQueueSheet()
                .environmentObject(audioManager)
                .presentationDetents([.medium, .large])
        }
    }

    // MARK: - Background Gradient
    private var backgroundGradient: some View {
        ZStack {
            // Base dark background
            AppColors.background

            // Dynamic gradient from artwork colors
            if let episode = audioManager.currentEpisode {
                LinearGradient(
                    colors: [
                        episode.artworkColors.first?.opacity(0.6) ?? .clear,
                        episode.artworkColors.first?.opacity(0.3) ?? .clear,
                        AppColors.background
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
        }
    }

    // MARK: - Header Section
    private var headerSection: some View {
        VStack(spacing: AppSpacing.lg) {
            // Drag indicator
            Capsule()
                .fill(Color.white.opacity(0.3))
                .frame(width: 36, height: 5)
                .padding(.top, AppSpacing.md)

            // Header row
            HStack {
                // Collapse button
                Button(action: {
                    audioManager.collapsePlayer()
                }) {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 20, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 44, height: 44)
                }

                Spacer()

                // Category label
                if let episode = audioManager.currentEpisode {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: episode.category.icon)
                            .font(.system(size: 12, weight: .medium))
                        Text(episode.category.rawValue.uppercased())
                            .font(AppTypography.captionBold)
                            .tracking(0.8)
                    }
                    .foregroundColor(episode.category.accentColor)
                }

                Spacer()

                // More options
                Button(action: {
                    // Show more options menu
                }) {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 20, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 44, height: 44)
                }
            }
        }
    }

    // MARK: - Artwork Section
    private var artworkSection: some View {
        Group {
            if let episode = audioManager.currentEpisode {
                AudioArtworkLarge(episode: episode, size: 280)
                    .scaleEffect(audioManager.isPlaying ? 1.0 : 0.95)
                    .animation(.spring(response: 0.4), value: audioManager.isPlaying)
            }
        }
    }

    // MARK: - Title Section
    private var titleSection: some View {
        VStack(spacing: AppSpacing.sm) {
            if let episode = audioManager.currentEpisode {
                Text(episode.title)
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)

                Text(episode.authorName)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Progress Section
    private var progressSection: some View {
        VStack(spacing: AppSpacing.sm) {
            // Progress slider
            AudioProgressSlider(
                progress: audioManager.progress,
                onSeek: { progress in
                    audioManager.seekToProgress(progress)
                }
            )

            // Time labels
            HStack {
                Text(audioManager.formattedCurrentTime)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .monospacedDigit()

                Spacer()

                Text(audioManager.formattedRemainingTime)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .monospacedDigit()
            }
        }
    }

    // MARK: - Main Controls Section
    private var controlsSection: some View {
        HStack(spacing: AppSpacing.xxxl) {
            // Skip backward 15s
            Button(action: {
                audioManager.skipBackward()
            }) {
                ZStack {
                    Image(systemName: "gobackward.15")
                        .font(.system(size: 28, weight: .medium))
                        .foregroundColor(AppColors.textPrimary)
                }
                .frame(width: 56, height: 56)
            }
            .buttonStyle(PlainButtonStyle())

            // Play/Pause
            Button(action: {
                audioManager.togglePlayPause()
            }) {
                ZStack {
                    Circle()
                        .fill(AppColors.textPrimary)
                        .frame(width: 72, height: 72)

                    Image(systemName: audioManager.isPlaying ? "pause.fill" : "play.fill")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundColor(AppColors.background)
                        .offset(x: audioManager.isPlaying ? 0 : 2)
                }
            }
            .buttonStyle(PlainButtonStyle())

            // Skip forward 15s
            Button(action: {
                audioManager.skipForward()
            }) {
                ZStack {
                    Image(systemName: "goforward.15")
                        .font(.system(size: 28, weight: .medium))
                        .foregroundColor(AppColors.textPrimary)
                }
                .frame(width: 56, height: 56)
            }
            .buttonStyle(PlainButtonStyle())
        }
    }

    // MARK: - Secondary Controls
    private var secondaryControlsSection: some View {
        HStack {
            // Playback speed
            Button(action: { showSpeedPicker = true }) {
                VStack(spacing: AppSpacing.xxs) {
                    Text(audioManager.playbackSpeed.label)
                        .font(AppTypography.captionBold)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Speed")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(width: 56)
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()

            // Sleep timer
            Button(action: { showSleepTimer = true }) {
                VStack(spacing: AppSpacing.xxs) {
                    Image(systemName: audioManager.sleepTimer == .off ? "moon" : "moon.fill")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(audioManager.sleepTimer == .off ? AppColors.textPrimary : AppColors.primaryBlue)
                    Text("Sleep")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(width: 56)
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()

            // Queue
            Button(action: { showQueue = true }) {
                VStack(spacing: AppSpacing.xxs) {
                    Image(systemName: "list.bullet")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textPrimary)
                    Text("Queue")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(width: 56)
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()

            // Share
            Button(action: {
                // Share action
            }) {
                VStack(spacing: AppSpacing.xxs) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textPrimary)
                    Text("Share")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .frame(width: 56)
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Audio Progress Slider
struct AudioProgressSlider: View {
    let progress: Double
    var onSeek: ((Double) -> Void)?

    @State private var isDragging: Bool = false
    @State private var dragProgress: Double = 0

    private var displayProgress: Double {
        isDragging ? dragProgress : progress
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                // Track background
                Capsule()
                    .fill(Color.white.opacity(0.2))
                    .frame(height: 4)

                // Progress fill
                Capsule()
                    .fill(AppColors.textPrimary)
                    .frame(width: geometry.size.width * displayProgress, height: 4)

                // Thumb (visible on drag)
                if isDragging {
                    Circle()
                        .fill(AppColors.textPrimary)
                        .frame(width: 16, height: 16)
                        .offset(x: (geometry.size.width * displayProgress) - 8)
                }
            }
            .frame(height: 24)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        isDragging = true
                        let newProgress = max(0, min(1, value.location.x / geometry.size.width))
                        dragProgress = newProgress
                    }
                    .onEnded { value in
                        let finalProgress = max(0, min(1, value.location.x / geometry.size.width))
                        onSeek?(finalProgress)
                        isDragging = false
                    }
            )
        }
        .frame(height: 24)
    }
}

// MARK: - Playback Speed Sheet
struct PlaybackSpeedSheet: View {
    @EnvironmentObject private var audioManager: AudioManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(PlaybackSpeed.allCases) { speed in
                    Button(action: {
                        audioManager.playbackSpeed = speed
                        dismiss()
                    }) {
                        HStack {
                            Text(speed.label)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)

                            Spacer()

                            if audioManager.playbackSpeed == speed {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .padding(.vertical, AppSpacing.xs)
                    }
                }
            }
            .listStyle(.plain)
            .navigationTitle("Playback Speed")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
    }
}

// MARK: - Sleep Timer Sheet
struct SleepTimerSheet: View {
    @EnvironmentObject private var audioManager: AudioManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(SleepTimerOption.allCases) { option in
                    Button(action: {
                        audioManager.sleepTimer = option
                        dismiss()
                    }) {
                        HStack {
                            Text(option.label)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)

                            Spacer()

                            if audioManager.sleepTimer == option {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 14, weight: .semibold))
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .padding(.vertical, AppSpacing.xs)
                    }
                }
            }
            .listStyle(.plain)
            .navigationTitle("Sleep Timer")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
    }
}

// MARK: - Audio Queue Sheet
struct AudioQueueSheet: View {
    @EnvironmentObject private var audioManager: AudioManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack {
                if audioManager.queue.isEmpty {
                    emptyQueueView
                } else {
                    queueList
                }
            }
            .navigationTitle("Up Next")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if !audioManager.queue.isEmpty {
                        Button("Clear") {
                            audioManager.clearQueue()
                        }
                        .foregroundColor(AppColors.bearish)
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
    }

    private var emptyQueueView: some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "list.bullet")
                .font(.system(size: 48, weight: .light))
                .foregroundColor(AppColors.textMuted)

            Text("Your queue is empty")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)

            Text("Add episodes from Money Moves or Books")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var queueList: some View {
        List {
            ForEach(Array(audioManager.queue.enumerated()), id: \.element.id) { index, item in
                HStack(spacing: AppSpacing.md) {
                    AudioArtworkThumbnail(episode: item.episode, size: 48)

                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text(item.episode.title)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(1)

                        Text(item.episode.authorName)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    Spacer()

                    Text(item.episode.formattedDuration)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .swipeActions(edge: .trailing) {
                    Button(role: .destructive) {
                        audioManager.removeFromQueue(at: index)
                    } label: {
                        Label("Remove", systemImage: "trash")
                    }
                }
            }
            .onMove { from, to in
                // Handle reordering
            }
        }
        .listStyle(.plain)
    }
}

// MARK: - Preview
#Preview {
    FullScreenAudioPlayer()
        .environmentObject(AudioManager.shared)
        .onAppear {
            AudioManager.shared.play(.sampleMoneyMoves)
        }
        .preferredColorScheme(.dark)
}
