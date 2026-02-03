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

    // UI State
    @Published var isMiniPlayerExpanded: Bool = false
    @Published var showFullScreenPlayer: Bool = false

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
    private var playbackTimer: Timer?
    private var sleepTimerInstance: Timer?
    private var cancellables = Set<AnyCancellable>()

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

        currentEpisode = episode
        duration = episode.duration
        currentTime = 0
        playbackState = .paused
    }

    /// Play a new episode
    func play(_ episode: AudioEpisode) {
        // Add current episode to history if exists
        if let current = currentEpisode {
            addToHistory(current)
        }

        currentEpisode = episode
        duration = episode.duration
        currentTime = 0
        playbackState = .loading

        // Simulate loading delay then start playing
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.playbackState = .playing
            self?.startPlaybackTimer()
        }
    }

    /// Resume playback
    func resume() {
        guard currentEpisode != nil else { return }
        playbackState = .playing
        startPlaybackTimer()
    }

    /// Pause playback
    func pause() {
        playbackState = .paused
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
        currentTime = max(0, min(time, duration))
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
        // In real implementation, update AVPlayer rate
        // For simulation, this affects the timer interval
        if playbackState == .playing {
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
