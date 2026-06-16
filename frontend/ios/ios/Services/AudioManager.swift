//
//  AudioManager.swift
//  ios
//
//  Central state management for the Global Audio Player
//  Injected environment-wide to enable playback control from anywhere in the app
//

import Foundation
import SwiftUI
import Combine
import AVFoundation

// MARK: - Audio Manager
@MainActor
final class AudioManager: ObservableObject {
    // MARK: - Singleton (for environment injection)
    static let shared = AudioManager()

    // MARK: - Published State
    @Published private(set) var currentEpisode: AudioEpisode?
    @Published private(set) var playbackState: PlaybackState = .idle
    @Published private(set) var currentTime: TimeInterval = 0
    @Published private(set) var duration: TimeInterval = 0
    @Published var playbackSpeed: PlaybackSpeed = .normal
    @Published var sleepTimer: SleepTimerOption = .off
    @Published private(set) var sleepTimerRemaining: TimeInterval = 0

    // Queue management
    @Published private(set) var queue: [AudioQueueItem] = []
    @Published private(set) var playbackHistory: [AudioEpisode] = []

    // Playback completion publisher - fires when current episode finishes naturally
    let playbackDidComplete = PassthroughSubject<AudioEpisode, Never>()

    // UI State
    @Published var isMiniPlayerExpanded: Bool = false
    @Published var showFullScreenPlayer: Bool = false
    @Published var isCompactMode: Bool = false  // True when chat keyboard is active (shows status island)
    @Published var isPlayerHiddenByScroll: Bool = false  // True when scroll-based hiding is active

    // MARK: - Computed Properties
    var isPlaying: Bool {
        playbackState == .playing
    }

    var hasActiveEpisode: Bool {
        currentEpisode != nil && playbackState.isActive
    }

    var progress: Double {
        guard duration > 0 else { return 0 }
        return currentTime / duration
    }

    var remainingTime: TimeInterval {
        max(0, duration - currentTime)
    }

    var formattedCurrentTime: String {
        formatTime(currentTime)
    }

    var formattedRemainingTime: String {
        "-" + formatTime(remainingTime)
    }

    var formattedDuration: String {
        formatTime(duration)
    }

    // MARK: - Private Properties
    private var playbackTimer: Timer?          // drives the simulated fallback (URL-less episodes)
    private var sleepTimerInstance: Timer?
    private var cancellables = Set<AnyCancellable>()

    // Real playback — used when an episode carries an audioUrl. URL-less episodes
    // (e.g. Books / Daily Brief without narration) keep the simulated timer path below.
    private var player: AVPlayer?
    private var timeObserver: Any?
    private var endObserver: NSObjectProtocol?

    // Audio session configuration
    private let audioSession = AVAudioSession.sharedInstance()

    // MARK: - Initialization
    private init() {
        setupAudioSession()
        setupObservers()
    }

    // MARK: - Audio Session Setup
    private func setupAudioSession() {
        do {
            try audioSession.setCategory(.playback, mode: .spokenAudio, options: [.allowBluetoothA2DP, .allowAirPlay])
            try audioSession.setActive(true)
        } catch {
            print("Failed to setup audio session: \(error)")
        }
    }

    private func setupObservers() {
        // Observe playback speed changes
        $playbackSpeed
            .sink { [weak self] speed in
                self?.updatePlaybackSpeed(speed)
            }
            .store(in: &cancellables)

        // Observe sleep timer changes
        $sleepTimer
            .sink { [weak self] option in
                self?.configureSleepTimer(option)
            }
            .store(in: &cancellables)
    }

    // MARK: - Playback Controls

    /// Load a new episode without starting playback (starts paused)
    func load(_ episode: AudioEpisode) {
        // Add current episode to history if exists
        if let current = currentEpisode {
            addToHistory(current)
        }

        teardownPlayer()
        stopPlaybackTimer()
        currentEpisode = episode
        duration = episode.duration
        currentTime = 0
        playbackState = .paused

        // Prepare (but don't start) a real player when narration is available.
        if let urlString = episode.audioUrl, let url = URL(string: urlString) {
            preparePlayer(url: url)
        }
    }

    /// Play a new episode
    func play(_ episode: AudioEpisode) {
        // Add current episode to history if exists
        if let current = currentEpisode {
            addToHistory(current)
        }

        teardownPlayer()
        stopPlaybackTimer()
        currentEpisode = episode
        duration = episode.duration
        currentTime = 0
        playbackState = .loading

        if let urlString = episode.audioUrl, let url = URL(string: urlString) {
            // Real playback via AVPlayer (the periodic time observer drives currentTime/duration).
            preparePlayer(url: url)
            player?.playImmediately(atRate: Float(playbackSpeed.rawValue))
            playbackState = .playing
        } else {
            // No narration URL: keep the legacy simulated progress so non-audio episodes
            // (e.g. Books / Daily Brief) behave exactly as before.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                guard let self, self.player == nil else { return }
                self.playbackState = .playing
                self.startPlaybackTimer()
            }
        }
    }

    /// Play an episode starting at a time offset. Used for one-file book narration that jumps to a
    /// core's start. Seeks BEFORE playing so the listener doesn't hear a moment of audio from 0:00.
    func play(_ episode: AudioEpisode, startAt: TimeInterval) {
        if let current = currentEpisode {
            addToHistory(current)
        }

        teardownPlayer()
        stopPlaybackTimer()
        currentEpisode = episode
        duration = episode.duration
        currentTime = max(0, startAt)
        playbackState = .loading

        if let urlString = episode.audioUrl, let url = URL(string: urlString) {
            preparePlayer(url: url)
            if startAt > 0 {
                player?.seek(to: CMTime(seconds: startAt, preferredTimescale: 600))
            }
            player?.playImmediately(atRate: Float(playbackSpeed.rawValue))
            playbackState = .playing
        } else {
            // No narration URL: simulated progress, advancing from the requested offset.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                guard let self, self.player == nil else { return }
                self.playbackState = .playing
                self.startPlaybackTimer()
            }
        }
    }

    /// Resume playback
    func resume() {
        guard currentEpisode != nil else { return }
        playbackState = .playing
        if let player {
            player.playImmediately(atRate: Float(playbackSpeed.rawValue))
        } else {
            startPlaybackTimer()
        }
    }

    /// Pause playback
    func pause() {
        playbackState = .paused
        player?.pause()
        stopPlaybackTimer()
    }

    /// Toggle play/pause
    func togglePlayPause() {
        switch playbackState {
        case .playing:
            pause()
        case .paused:
            resume()
        case .idle:
            if let episode = currentEpisode {
                play(episode)
            }
        default:
            break
        }
    }

    /// Stop playback and clear current episode
    func stop() {
        stopPlaybackTimer()
        teardownPlayer()
        stopSleepTimer()
        playbackState = .idle

        // Add to history before clearing
        if let episode = currentEpisode {
            addToHistory(episode)
        }

        currentEpisode = nil
        currentTime = 0
        duration = 0
        showFullScreenPlayer = false
    }

    /// Seek to specific time
    func seek(to time: TimeInterval) {
        let clamped = max(0, min(time, duration))
        currentTime = clamped
        player?.seek(to: CMTime(seconds: clamped, preferredTimescale: 600))
    }

    /// Seek by offset (positive = forward, negative = backward)
    func seekBy(_ offset: TimeInterval) {
        seek(to: currentTime + offset)
    }

    /// Skip forward 15 seconds
    func skipForward() {
        seekBy(15)
    }

    /// Skip backward 15 seconds
    func skipBackward() {
        seekBy(-15)
    }

    /// Seek to progress (0.0 - 1.0)
    func seekToProgress(_ progress: Double) {
        let clampedProgress = max(0, min(1, progress))
        seek(to: duration * clampedProgress)
    }

    // MARK: - Queue Management

    /// Add episode to queue
    func addToQueue(_ episode: AudioEpisode) {
        let item = AudioQueueItem(episode: episode)
        queue.append(item)
    }

    /// Remove from queue
    func removeFromQueue(at index: Int) {
        guard queue.indices.contains(index) else { return }
        queue.remove(at: index)
    }

    /// Play next in queue
    func playNext() {
        guard !queue.isEmpty else {
            stop()
            return
        }

        let nextItem = queue.removeFirst()
        play(nextItem.episode)
    }

    /// Clear queue
    func clearQueue() {
        queue.removeAll()
    }

    // MARK: - Speed Control

    private func updatePlaybackSpeed(_ speed: PlaybackSpeed) {
        if let player {
            // A non-zero rate also resumes playback, so only apply it while playing.
            if playbackState == .playing {
                player.rate = Float(speed.rawValue)
            }
        } else if playbackState == .playing {
            // Simulated fallback: re-arm the timer at the new interval.
            stopPlaybackTimer()
            startPlaybackTimer()
        }
    }

    // MARK: - Sleep Timer

    private func configureSleepTimer(_ option: SleepTimerOption) {
        stopSleepTimer()

        guard option != .off else {
            sleepTimerRemaining = 0
            return
        }

        if option == .endOfEpisode {
            sleepTimerRemaining = remainingTime
        } else {
            sleepTimerRemaining = TimeInterval(option.rawValue * 60)
        }

        startSleepTimer()
    }

    private func startSleepTimer() {
        sleepTimerInstance = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                if self.sleepTimerRemaining > 0 {
                    self.sleepTimerRemaining -= 1
                } else {
                    self.pause()
                    self.sleepTimer = .off
                    self.stopSleepTimer()
                }
            }
        }
    }

    private func stopSleepTimer() {
        sleepTimerInstance?.invalidate()
        sleepTimerInstance = nil
    }

    // MARK: - Real Playback (AVPlayer)

    /// Build an AVPlayer for the URL and attach observers that mirror playback into the
    /// published state the UI binds to (currentTime, real duration, completion). Does not start.
    private func preparePlayer(url: URL) {
        teardownPlayer()
        let item = AVPlayerItem(url: url)
        let newPlayer = AVPlayer(playerItem: item)
        newPlayer.automaticallyWaitsToMinimizeStalling = true
        player = newPlayer

        // Mirror playback position (and the real duration once known) into published state.
        let interval = CMTime(seconds: 0.5, preferredTimescale: 600)
        timeObserver = newPlayer.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            Task { @MainActor [weak self] in
                guard let self, self.player != nil else { return }
                let secs = time.seconds
                if secs.isFinite { self.currentTime = secs }
                if let itemDuration = self.player?.currentItem?.duration.seconds,
                   itemDuration.isFinite, itemDuration > 0 {
                    self.duration = itemDuration
                }
            }
        }

        // Advance the queue / settle state when the clip finishes naturally.
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.handlePlaybackComplete() }
        }
    }

    private func teardownPlayer() {
        if let timeObserver, let player {
            player.removeTimeObserver(timeObserver)
        }
        timeObserver = nil
        if let endObserver {
            NotificationCenter.default.removeObserver(endObserver)
        }
        endObserver = nil
        player?.pause()
        player = nil
    }

    // MARK: - Playback Timer (Simulation)

    private func startPlaybackTimer() {
        stopPlaybackTimer()

        let interval = 1.0 / playbackSpeed.rawValue
        playbackTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                if self.currentTime < self.duration {
                    self.currentTime += 1
                } else {
                    self.handlePlaybackComplete()
                }
            }
        }
    }

    private func stopPlaybackTimer() {
        playbackTimer?.invalidate()
        playbackTimer = nil
    }

    private func handlePlaybackComplete() {
        stopPlaybackTimer()
        teardownPlayer()

        // Notify listeners that the episode completed naturally
        if let episode = currentEpisode {
            playbackDidComplete.send(episode)
        }

        if !queue.isEmpty {
            playNext()
        } else {
            playbackState = .paused
            currentTime = duration
        }
    }

    // MARK: - History Management

    private func addToHistory(_ episode: AudioEpisode) {
        // Remove if already in history to avoid duplicates
        playbackHistory.removeAll { $0.id == episode.id }
        // Add to front
        playbackHistory.insert(episode, at: 0)
        // Keep only last 50 items
        if playbackHistory.count > 50 {
            playbackHistory = Array(playbackHistory.prefix(50))
        }
    }

    // MARK: - UI Actions

    /// Expand mini player to full screen
    func expandPlayer() {
        withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
            showFullScreenPlayer = true
        }
    }

    /// Collapse full screen player to mini player
    func collapsePlayer() {
        withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
            showFullScreenPlayer = false
        }
    }

    /// Enter compact mode (status island at top)
    func enterCompactMode() {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
            isCompactMode = true
        }
    }

    /// Exit compact mode (show full mini player at bottom)
    func exitCompactMode() {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
            isCompactMode = false
        }
    }

    /// Hide player via scroll (used by detail screens)
    func hidePlayerByScroll() {
        guard !isPlayerHiddenByScroll else { return }
        withAnimation(.easeInOut(duration: 0.25)) {
            isPlayerHiddenByScroll = true
        }
    }

    /// Show player after scroll hiding
    func showPlayerAfterScroll() {
        guard isPlayerHiddenByScroll else { return }
        withAnimation(.easeInOut(duration: 0.25)) {
            isPlayerHiddenByScroll = false
        }
    }

    /// Reset scroll hiding state (call when leaving detail screens)
    func resetScrollHiding() {
        isPlayerHiddenByScroll = false
    }

    // MARK: - Helpers

    private func formatTime(_ time: TimeInterval) -> String {
        let totalSeconds = Int(time)
        let hours = totalSeconds / 3600
        let minutes = (totalSeconds % 3600) / 60
        let seconds = totalSeconds % 60

        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, seconds)
        } else {
            return String(format: "%d:%02d", minutes, seconds)
        }
    }
}

// MARK: - Environment Key
struct AudioManagerKey: EnvironmentKey {
    static let defaultValue: AudioManager = AudioManager.shared
}

extension EnvironmentValues {
    var audioManager: AudioManager {
        get { self[AudioManagerKey.self] }
        set { self[AudioManagerKey.self] = newValue }
    }
}

// MARK: - View Extension for Easy Access
extension View {
    func withAudioManager() -> some View {
        self.environmentObject(AudioManager.shared)
    }
}
