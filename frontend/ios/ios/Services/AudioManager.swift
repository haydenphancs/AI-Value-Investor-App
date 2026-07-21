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
import MediaPlayer
import UIKit

// MARK: - Audio Manager
@MainActor
final class AudioManager: ObservableObject {
    // MARK: - Singleton (for environment injection)
    static let shared = AudioManager()

    // MARK: - Device capability
    @MainActor private static var _hasDynamicIsland: Bool?
    /// Whether this device has a Dynamic Island, i.e. whether the system Now Playing is shown at the
    /// top WHILE IN-APP. Detected from the key window's top safe-area inset (~59pt DI, ~47 notch,
    /// ~20 others). Cached once a real inset is available. On non-DI devices we keep an in-app
    /// indicator (the system audio surfaces only on the Lock Screen / Control Center there).
    @MainActor static var hasDynamicIsland: Bool {
        if let cached = _hasDynamicIsland { return cached }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let window = scenes.flatMap { $0.windows }.first { $0.isKeyWindow } ?? scenes.flatMap { $0.windows }.first
        guard let top = window?.safeAreaInsets.top, top > 0 else { return false } // window not ready yet
        let result = top >= 51
        _hasDynamicIsland = result
        return result
    }

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
    @Published private(set) var isCompactMode: Bool = false  // True when collapsed to the top status island
    @Published var isPlayerHiddenByScroll: Bool = false  // True when scroll-based hiding is active

    // Compact-mode is requested by several independent drivers (Wiser chat-bar focus, each stock
    // screen's lifetime, ChatTabView). Track them as a reason set keyed by a stable per-screen token
    // so they can't stomp each other and so duplicate appear/disappear or focus on/off are idempotent.
    // `isCompactMode` is true while ANY reason is active.
    private var compactReasons: Set<String> = []

    // MARK: - Computed Properties
    var isPlaying: Bool {
        playbackState == .playing
    }

    /// Drives whether `GlobalAudioOverlay` mounts the mini/compact player at all.
    ///
    /// `.error` is deliberately treated as active even though `PlaybackState.isActive` excludes it:
    /// a 404 / expired Storage URL used to unmount the whole player, so the audio appeared to simply
    /// VANISH mid-tap and the retry affordance (the play button → `togglePlayPause` → `.error` case)
    /// became unreachable. Keeping the episode mounted is what makes that retry reachable.
    var hasActiveEpisode: Bool {
        currentEpisode != nil && (playbackState.isActive || isPlaybackErrored)
    }

    /// The user-facing failure message while the current episode is in `.error`, for any surface that
    /// wants to explain the stalled player rather than silently showing a dead play button.
    var playbackErrorMessage: String? {
        if case .error(let message) = playbackState { return message }
        return nil
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
    // Detect a failed load (404 / expired / malformed Storage object / bad URL) so the player
    // surfaces .error instead of sitting in a false "playing" state with silence forever.
    private var statusObserver: NSKeyValueObservation?
    private var failedToEndObserver: NSObjectProtocol?
    // A mid-stream STALL (Airplane Mode, dead Wi-Fi) never fails the item — AVPlayer just stops
    // advancing while the requested rate stands. Observed so the UI/Lock Screen don't keep claiming
    // playback against a frozen playhead.
    private var timeControlObserver: NSKeyValueObservation?

    // Audio session configuration
    private let audioSession = AVAudioSession.sharedInstance()
    // Whether WE currently hold the (non-mixable) shared session. Tracked so activation is taken
    // lazily — at first real playback — and released with `.notifyOthersOnDeactivation` afterwards.
    private var isSessionActive = false

    // Rendered gradient artwork per episode, for the system Now Playing (Dynamic Island / Lock
    // Screen / Control Center). Cached so it's built once per episode.
    private var artworkCache: [String: MPMediaItemArtwork] = [:]

    // Whether playback was active when a system interruption (call / Siri) began — so .ended only
    // resumes audio the user was actually playing, not something they'd paused or a finished episode.
    private var wasPlayingBeforeInterruption = false

    // The in-flight seek target (real-player only). While set, the periodic observer ignores stale
    // pre-seek ticks so the scrubber doesn't flick backward after a scrub. Cleared on seek completion.
    private var pendingSeekTarget: TimeInterval?

    // MARK: - Initialization
    private init() {
        configureAudioSession()
        setupObservers()
        setupRemoteCommands()
        setupAudioSessionInterruptionObservers()
    }

    // MARK: - Audio Session Setup

    /// Declare HOW we'd play, without taking the session. `RootContainerView` holds this singleton,
    /// so init runs the moment the app launches — activating here stopped the user's Spotify /
    /// Podcasts before they had tapped anything. Category is cheap and non-disruptive; the
    /// disruptive part (`setActive(true)`) is deferred to `activateAudioSession()`.
    private func configureAudioSession() {
        do {
            try audioSession.setCategory(.playback, mode: .spokenAudio, options: [.allowBluetoothA2DP, .allowAirPlay])
        } catch {
            print("[AudioManager] audio session category setup failed: \(error)")
        }
    }

    /// Take the shared session — called only when REAL audio is about to start (a prepared AVPlayer).
    /// The URL-less simulated path makes no sound, so it must never evict another app's audio.
    /// Re-asserts category+mode because `AIVoiceManager` drives Journey narration on the same shared
    /// session; whichever engine speaks last would otherwise leave its own mode in place.
    private func activateAudioSession() {
        do {
            try audioSession.setCategory(.playback, mode: .spokenAudio, options: [.allowBluetoothA2DP, .allowAirPlay])
            try audioSession.setActive(true)
            isSessionActive = true
        } catch {
            print("[AudioManager] audio session activation failed: \(error)")
        }
    }

    /// Release the session so whatever was playing before us (Spotify, Podcasts) can resume.
    /// Deliberately NOT called from `pause()`: a paused episode keeps its Lock Screen / Dynamic
    /// Island transport, and the user is one tap away from resuming.
    private func deactivateAudioSession() {
        guard isSessionActive else { return }
        // AIVoiceManager narrates Investor Journey on this SAME session. If it took over (it calls
        // pauseForExternalAudio on us first), releasing the session here would cut ITS audio off —
        // so treat that as a handover: we no longer own the session, but it stays active.
        guard !AIVoiceManager.shared.isPlaying else {
            isSessionActive = false
            return
        }
        do {
            try audioSession.setActive(false, options: [.notifyOthersOnDeactivation])
            isSessionActive = false
        } catch {
            print("[AudioManager] audio session deactivation failed: \(error)")
        }
    }

    /// Keep playback state + Now Playing correct across system interruptions (calls / Siri) and audio
    /// route changes (e.g. unplugging headphones) — expected of any background-audio app. Observers
    /// live for the app lifetime (this is a singleton), so they're never removed.
    private func setupAudioSessionInterruptionObservers() {
        let nc = NotificationCenter.default
        nc.addObserver(forName: AVAudioSession.interruptionNotification, object: audioSession, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            let optionRaw = (note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt) ?? 0
            let shouldResume = AVAudioSession.InterruptionOptions(rawValue: optionRaw).contains(.shouldResume)
            Task { @MainActor [weak self] in self?.handleInterruption(type: type, shouldResume: shouldResume) }
        }
        nc.addObserver(forName: AVAudioSession.routeChangeNotification, object: audioSession, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: raw) else { return }
            Task { @MainActor [weak self] in self?.handleRouteChange(reason: reason) }
        }
    }

    private func handleInterruption(type: AVAudioSession.InterruptionType, shouldResume: Bool) {
        switch type {
        case .began:
            // Remember whether we were actually playing, THEN reflect the system pause.
            wasPlayingBeforeInterruption = (playbackState == .playing)
            if playbackState == .playing { pause() }
        case .ended:
            // Resume only if the system permits AND we were playing when it began — `.shouldResume`
            // alone means "you MAY resume", not "you were playing" (a user-paused or finished
            // episode would otherwise auto-start after a call).
            if shouldResume, wasPlayingBeforeInterruption, currentEpisode != nil {
                // The interruption deactivated the session out from under us; resume() re-takes it.
                isSessionActive = false
                resume()
            }
            wasPlayingBeforeInterruption = false
        @unknown default:
            break
        }
    }

    private func handleRouteChange(reason: AVAudioSession.RouteChangeReason) {
        // Headphones / Bluetooth disconnected → pause, like Music / Podcasts / Spotify.
        if reason == .oldDeviceUnavailable, playbackState == .playing {
            pause()
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

        // Mirror playback state + speed into the system Now Playing (Dynamic Island / Lock Screen).
        // `.receive(on:)` defers delivery until AFTER the @Published property has settled, so the
        // handler reads the current value (publishers fire in willSet, before the property updates).
        $playbackState
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.updateNowPlayingInfo() }
            .store(in: &cancellables)
        $playbackSpeed
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.updateNowPlayingInfo() }
            .store(in: &cancellables)
    }

    // MARK: - System Now Playing (Dynamic Island / Lock Screen / Control Center)

    /// Wire the Control Center / Lock Screen / Dynamic Island / headphone transport buttons to our
    /// playback. Remote command handlers are delivered on the main thread; we hop to the main actor
    /// defensively and return `.success` immediately.
    private func setupRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()

        center.playCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.resume() }
            return .success
        }
        center.pauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.pause() }
            return .success
        }
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.togglePlayPause() }
            return .success
        }
        center.skipForwardCommand.preferredIntervals = [15]
        center.skipForwardCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.skipForward() }
            return .success
        }
        center.skipBackwardCommand.preferredIntervals = [15]
        center.skipBackwardCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.skipBackward() }
            return .success
        }
        center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let positionEvent = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            let time = positionEvent.positionTime
            Task { @MainActor [weak self] in self?.seek(to: time) }
            return .success
        }
        // We expose 15s skip, not track-to-track.
        center.nextTrackCommand.isEnabled = false
        center.previousTrackCommand.isEnabled = false
    }

    /// True while the current episode is in the failed (.error) state.
    private var isPlaybackErrored: Bool {
        if case .error = playbackState { return true }
        return false
    }

    /// Publish the current episode + playback state to the system so it renders in the Dynamic
    /// Island / Lock Screen / Control Center. Call on every state change (load/play/pause/seek/speed).
    private func updateNowPlayingInfo() {
        // Clear on failure too (not only when there's no episode): handlePlaybackFailure leaves
        // currentEpisode set, and the deferred $playbackState sink would otherwise republish the dead
        // episode, leaving a stale/frozen entry in the Dynamic Island / Lock Screen.
        guard let episode = currentEpisode, !isPlaybackErrored else {
            clearNowPlayingInfo()
            return
        }

        // Clamp before publishing — malformed server data (e.g. a negative audio_duration_seconds)
        // must not produce a broken/backwards system scrubber.
        let safeDuration = max(0, duration)
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: episode.title.isEmpty ? "Caydex" : episode.title,
            MPMediaItemPropertyArtist: episode.authorName.isEmpty ? "Cay AI by Caydex" : episode.authorName,
            MPMediaItemPropertyPlaybackDuration: safeDuration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: min(max(0, currentTime), safeDuration),
            // iOS extrapolates elapsed time from the rate, so we don't need per-tick updates.
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? playbackSpeed.rawValue : 0.0,
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
        ]

        if let artwork = artwork(for: episode) {
            info[MPMediaItemPropertyArtwork] = artwork
        }

        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func clearNowPlayingInfo() {
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    /// Lazily render (and cache) a gradient + icon artwork image from the episode's theme so the
    /// Now Playing surfaces show something on-brand instead of a blank tile.
    private func artwork(for episode: AudioEpisode) -> MPMediaItemArtwork? {
        if let cached = artworkCache[episode.id] { return cached }

        let size = CGSize(width: 600, height: 600)
        var uiColors: [UIColor] = episode.artworkGradientColors.map { UIColor(Color(hex: $0)) }
        if uiColors.isEmpty { uiColors = [.black, .darkGray] }
        if uiColors.count == 1 { uiColors.append(uiColors[0]) } // CGGradient needs ≥ 2 stops
        let cgColors = uiColors.map { $0.cgColor }

        let renderer = UIGraphicsImageRenderer(size: size)
        let image = renderer.image { ctx in
            let cg = ctx.cgContext
            let locations: [CGFloat] = (0..<cgColors.count).map { CGFloat($0) / CGFloat(cgColors.count - 1) }
            if let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                                         colors: cgColors as CFArray, locations: locations) {
                cg.drawLinearGradient(gradient, start: .zero,
                                      end: CGPoint(x: size.width, y: size.height), options: [])
            }
            let config = UIImage.SymbolConfiguration(pointSize: size.width * 0.30, weight: .semibold)
            if let icon = UIImage(systemName: episode.artworkIcon, withConfiguration: config)?
                .withTintColor(.white, renderingMode: .alwaysOriginal) {
                let rect = CGRect(x: (size.width - icon.size.width) / 2,
                                  y: (size.height - icon.size.height) / 2,
                                  width: icon.size.width, height: icon.size.height)
                icon.draw(in: rect)
            }
        }

        let artwork = MPMediaItemArtwork(boundsSize: size) { _ in image }
        artworkCache[episode.id] = artwork
        return artwork
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
            activateAudioSession()
            preparePlayer(url: url)
            player?.playImmediately(atRate: Float(playbackSpeed.rawValue))
            playbackState = .playing
        } else {
            // No narration URL: keep the legacy simulated progress so non-audio episodes
            // (e.g. Books / Daily Brief) behave exactly as before. Guard on (still loading, same
            // episode) so a pause/stop/switch during the 0.5s window cancels this stale start.
            let episodeID = episode.id
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                guard let self, self.player == nil,
                      self.playbackState == .loading, self.currentEpisode?.id == episodeID else { return }
                self.playbackState = .playing
                self.startPlaybackTimer()
                self.resumeSleepTimerIfNeeded()
            }
        }
        resumeSleepTimerIfNeeded()
    }

    /// Play an episode starting at a time offset. Used for one-file book narration that jumps to a
    /// core's start. Seeks BEFORE playing so the listener doesn't hear a moment of audio from 0:00.
    func play(_ episode: AudioEpisode, startAt: TimeInterval) {
        // Reverse of AIVoiceManager.pauseForExternalAudio: silence Journey
        // narration before taking the shared session, so a book or Money Moves
        // clip can never start on top of a lesson already speaking.
        AIVoiceManager.shared.stop()

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
            activateAudioSession()
            preparePlayer(url: url)
            seekPreparedPlayer(to: startAt)
            player?.playImmediately(atRate: Float(playbackSpeed.rawValue))
            playbackState = .playing
        } else {
            // No narration URL: simulated progress, advancing from the requested offset. Guard on
            // (still loading, same episode) so a pause/stop/switch during the 0.5s window cancels it.
            let episodeID = episode.id
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                guard let self, self.player == nil,
                      self.playbackState == .loading, self.currentEpisode?.id == episodeID else { return }
                self.playbackState = .playing
                self.startPlaybackTimer()
                self.resumeSleepTimerIfNeeded()
            }
        }
        resumeSleepTimerIfNeeded()
    }

    /// Resume playback
    func resume() {
        guard let episode = currentEpisode else { return }
        // Play tapped after the episode finished (e.g. from the Lock Screen) → restart from the top.
        if duration > 0, currentTime >= duration - 0.5 { currentTime = 0 }
        playbackState = .playing
        if let player {
            activateAudioSession()
            player.playImmediately(atRate: Float(playbackSpeed.rawValue))
        } else if let urlString = episode.audioUrl, let url = URL(string: urlString) {
            // The real player was torn down (after natural completion, or by handlePlaybackFailure
            // on the retry path) — rebuild it so resume produces ACTUAL audio rather than the silent
            // simulated fallback.
            activateAudioSession()
            preparePlayer(url: url)
            seekPreparedPlayer(to: currentTime)
            player?.playImmediately(atRate: Float(playbackSpeed.rawValue))
        } else {
            startPlaybackTimer()
        }
        resumeSleepTimerIfNeeded()
    }

    /// Pause playback
    func pause() {
        playbackState = .paused
        player?.pause()
        stopPlaybackTimer()
        // Suspend (don't clear) the sleep countdown: a 15-minute timer must not burn down while the
        // episode is paused. `resume()` re-arms it with the remaining time intact.
        stopSleepTimer()
    }

    /// Yield the shared audio session to the OTHER engine (AIVoiceManager, which
    /// drives Investor Journey narration).
    ///
    /// The two managers own separate AVPlayers on the same non-mixable
    /// `.playback` session and had no knowledge of each other, so starting a
    /// Journey lesson mid-book left both voices audible at once. Only pauses
    /// when something is actually playing, so this is safe to call
    /// unconditionally on every Journey playback entry point.
    func pauseForExternalAudio() {
        guard playbackState == .playing else { return }
        print("🔇 AudioManager: yielding the audio session to Journey narration")
        pause()
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
        case .error:
            // RETRY after a load/stream failure (404 / expired URL / mid-stream network drop).
            // handlePlaybackFailure tore the player down but left `currentEpisode` + `currentTime`
            // intact, and the full-screen player stays visible (it's gated on `showFullScreenPlayer`,
            // not `hasActiveEpisode`), so its play button must DO something rather than be a dead
            // no-op. resume() rebuilds the player at the last position via the exact same path it uses
            // to resume after natural completion; if the URL is still bad it simply returns to .error
            // and the user can tap again. This deliberately does NOT touch the load-bearing
            // `hasActiveEpisode` gating (the broader error/retry UI remains a separate follow-up).
            if currentEpisode != nil { resume() }
        case .loading:
            // .loading is also the STALLED state (see handleTimeControlStatusChange), which can last
            // as long as the network is down — the button must not be a dead no-op there. Pausing
            // during the 0.5s simulated-start window is equally correct: it cancels the stale start.
            pause()
        }
    }

    /// Stop playback and clear current episode
    func stop() {
        stopPlaybackTimer()
        teardownPlayer()
        // Clear the SELECTION, not just the Timer: invalidating alone left `sleepTimer` on e.g.
        // "30 minutes", so the full-screen player kept showing an armed moon badge for a countdown
        // that no longer existed — and the next episode then played straight through the night.
        // (Assigning .off routes through configureSleepTimer, which also zeroes `sleepTimerRemaining`.)
        sleepTimer = .off
        sleepTimerRemaining = 0
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

        // Drop any compact-mode requests so a later, unrelated episode doesn't inherit a stale
        // "collapsed to island" state. Screens re-assert compact on their next appear/focus.
        compactReasons.removeAll()
        isCompactMode = false

        // Nothing left to control → hand the session back so other apps' audio can resume.
        deactivateAudioSession()
    }

    /// Seek to specific time
    func seek(to time: TimeInterval) {
        let clamped = max(0, min(time, duration))
        currentTime = clamped
        if let player {
            // Suppress stale post-seek ticks until the player actually reaches the target.
            pendingSeekTarget = clamped
            player.seek(to: CMTime(seconds: clamped, preferredTimescale: 600)) { [weak self] finished in
                Task { @MainActor [weak self] in
                    guard let self, finished, self.pendingSeekTarget == clamped else { return }
                    self.pendingSeekTarget = nil
                }
            }
        }
        updateNowPlayingInfo() // scrubbing/skip doesn't change playbackState
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

        // Arming while paused must not burn the countdown; playback start re-arms it.
        if playbackState == .playing { startSleepTimer() }
    }

    /// Re-arm a still-selected sleep timer whenever playback (re)starts. `pause()` suspends the
    /// countdown rather than clearing it, so the timer measures listening time, not wall-clock time.
    private func resumeSleepTimerIfNeeded() {
        guard sleepTimer != .off, sleepTimerInstance == nil else { return }
        guard sleepTimer == .endOfEpisode || sleepTimerRemaining > 0 else { return }
        startSleepTimer()
    }

    private func startSleepTimer() {
        // Use .common run-loop mode so the countdown keeps firing while the user scrolls/interacts;
        // a plain scheduledTimer runs only in .default mode and would stall during UI tracking,
        // making the timer fire late (or appear not to stop).
        let timer = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                if self.sleepTimer == .endOfEpisode {
                    // "End of episode" is a MEDIA distance, not a wall-clock one. It was seeded from
                    // `remainingTime` (media seconds) but decremented once per real second, so at
                    // 1.5x/2x the episode ended long before the countdown did — and the timer then
                    // fired in the MIDDLE of the next episode. Re-read the true remaining media time
                    // each tick so the fire point tracks the playhead at any speed.
                    guard self.duration > 0 else { return }  // duration not settled yet — don't fire at 0
                    self.sleepTimerRemaining = self.remainingTime
                } else if self.sleepTimerRemaining > 0 {
                    self.sleepTimerRemaining -= 1
                }

                if self.sleepTimerRemaining <= 0 {
                    self.pause()          // also suspends this timer
                    self.sleepTimer = .off  // clears the armed moon badge (see stop())
                    self.stopSleepTimer()
                }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        sleepTimerInstance = timer
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
        timeObserver = newPlayer.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self, weak newPlayer] time in
            Task { @MainActor [weak self, weak newPlayer] in
                // Only honor ticks from the CURRENT player — a tick from a torn-down player must not
                // clobber the new episode's playhead (rapid replay/switch race).
                guard let self, let np = newPlayer, self.player === np else { return }
                let secs = time.seconds
                // Ignore stale ticks while a seek is settling, so the scrubber doesn't flick backward.
                if secs.isFinite, self.pendingSeekTarget == nil { self.currentTime = secs }
                // Adopt the real duration once it settles (only when it actually changes — avoids a
                // per-tick republish) and refresh the Lock Screen / Dynamic Island scrubber length.
                if let itemDuration = self.player?.currentItem?.duration.seconds,
                   itemDuration.isFinite, itemDuration > 0,
                   abs(self.duration - itemDuration) > 0.5 {
                    self.duration = itemDuration
                    // Re-sync an "End of episode" sleep timer to the real length (the estimate the
                    // user picked it against can be off for Money Moves narration).
                    if self.sleepTimer == .endOfEpisode { self.sleepTimerRemaining = self.remainingTime }
                    self.updateNowPlayingInfo()
                }
            }
        }

        // Advance the queue / settle state when the clip finishes naturally.
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.handlePlaybackComplete() }
        }

        // Surface load/playback FAILURES. Without these, a 404 / expired / malformed Storage
        // object (or a bad URL that still parses, e.g. a trailing "?") leaves item.status == .failed
        // while playbackState was optimistically set to .playing — the UI shows "playing" at 0:00
        // with no audio, no completion, and a frozen read-along, indefinitely.
        statusObserver = item.observe(\.status, options: [.new]) { [weak self, weak item] _, _ in
            guard let item else { return }
            Task { @MainActor [weak self] in self?.handleItemStatusChange(item) }
        }
        failedToEndObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime, object: item, queue: .main
        ) { [weak self] note in
            let err = note.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
            Task { @MainActor [weak self] in self?.handlePlaybackFailure(err) }
        }

        // Surface BUFFERING/STALLS. Nothing else observes them: on a mid-narration network drop the
        // item neither fails nor ends, so playbackState stayed .playing forever while currentTime
        // froze — and updateNowPlayingInfo kept publishing rate > 0, so the Lock Screen scrubber
        // happily extrapolated past the real (stuck) playhead.
        timeControlObserver = newPlayer.observe(\.timeControlStatus, options: [.new]) { [weak self, weak newPlayer] _, _ in
            Task { @MainActor [weak self, weak newPlayer] in
                guard let self, let np = newPlayer, self.player === np else { return }
                self.handleTimeControlStatusChange(np.timeControlStatus)
            }
        }
    }

    /// Reflect AVPlayer's own view of whether audio is actually flowing.
    /// `.loading` is reused as the stalled/buffering state: it keeps the player mounted
    /// (`PlaybackState.isActive`) while making `isPlaying` false, which drops the published Now
    /// Playing rate to 0 so the system scrubber stops running ahead of the truth.
    private func handleTimeControlStatusChange(_ status: AVPlayer.TimeControlStatus) {
        switch status {
        case .waitingToPlayAtSpecifiedRate:
            // Only downgrade an optimistic .playing — a user-initiated pause must stay .paused.
            if playbackState == .playing { playbackState = .loading }
        case .playing:
            if playbackState == .loading {
                playbackState = .playing
                resumeSleepTimerIfNeeded()   // the stall suspended it via pause()/arming-while-stalled
            }
        case .paused:
            break   // pause() / handlePlaybackComplete already own this transition
        @unknown default:
            break
        }
    }

    /// Seek a freshly-prepared player, suppressing stale pre-seek ticks exactly like `seek(to:)`.
    /// Without the `pendingSeekTarget` handshake the periodic observer's first tick publishes
    /// `currentTime = 0`, and a book-core jump visibly flashes the WRONG core before snapping back.
    private func seekPreparedPlayer(to time: TimeInterval) {
        guard let player, time > 0 else { return }
        pendingSeekTarget = time
        player.seek(to: CMTime(seconds: time, preferredTimescale: 600)) { [weak self] finished in
            Task { @MainActor [weak self] in
                guard let self, finished, self.pendingSeekTarget == time else { return }
                self.pendingSeekTarget = nil
            }
        }
    }

    private func teardownPlayer() {
        if let timeObserver, let player {
            player.removeTimeObserver(timeObserver)
        }
        timeObserver = nil
        statusObserver?.invalidate()
        statusObserver = nil
        timeControlObserver?.invalidate()
        timeControlObserver = nil
        if let endObserver {
            NotificationCenter.default.removeObserver(endObserver)
        }
        endObserver = nil
        if let failedToEndObserver {
            NotificationCenter.default.removeObserver(failedToEndObserver)
        }
        failedToEndObserver = nil
        player?.pause()
        player = nil
        pendingSeekTarget = nil
    }

    /// React to AVPlayerItem readiness. On `.failed`, surface a real error state (so the UI stops
    /// showing a false "playing"); on `.readyToPlay`, confirm playing if we were still loading.
    private func handleItemStatusChange(_ item: AVPlayerItem) {
        guard player?.currentItem === item else { return }   // ignore a torn-down item's late callback
        switch item.status {
        case .failed:
            handlePlaybackFailure(item.error)
        case .readyToPlay:
            // Don't promote out of a STALL: .loading now doubles as the buffering state, and a late
            // readyToPlay would otherwise claim playback while the playhead is still frozen.
            if playbackState == .loading, player?.timeControlStatus != .waitingToPlayAtSpecifiedRate {
                playbackState = .playing
            }
        default:
            break
        }
    }

    /// A load/playback failure on the current item: stop the false "playing", tear the player down,
    /// surface a typed `.error` state (isPlaying becomes false, so the mini-player / Lock Screen stop
    /// claiming playback), and log loudly for diagnosability.
    private func handlePlaybackFailure(_ error: Error?) {
        // Already torn down (e.g. status + failed-to-end both fired) — nothing to do.
        guard player != nil else { return }
        let appError = error.map(AppError.from) ?? .unknown(message: "This audio couldn't be played.")
        // Identify WHICH episode/URL died — a 404 on one Storage object is otherwise indistinguishable
        // from a general network outage in the logs.
        print("""
        [AudioManager] playback failed — episode: \(currentEpisode?.id ?? "nil") \
        url: \(currentEpisode?.audioUrl ?? "nil") \
        message: \(appError.message) raw: \(error.map { String(describing: $0) } ?? "nil")
        """)
        teardownPlayer()
        stopPlaybackTimer()
        stopSleepTimer()   // nothing is playing; don't burn a sleep countdown against dead audio
        playbackState = .error(appError.message)
        // The episode stays set (hasActiveEpisode keeps the player mounted for retry), but no audio
        // is flowing — let other apps have the session back until the user retries.
        deactivateAudioSession()
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

        // An armed "End of episode" timer means STOP here — advancing the queue would be exactly the
        // "woke up three episodes later" failure the option exists to prevent.
        let stopAtEndOfEpisode = (sleepTimer == .endOfEpisode)
        if stopAtEndOfEpisode {
            sleepTimer = .off
            stopSleepTimer()
        }

        // Notify listeners that the episode completed naturally
        if let episode = currentEpisode {
            playbackDidComplete.send(episode)
        }

        if !queue.isEmpty && !stopAtEndOfEpisode {
            playNext()
        } else {
            playbackState = .paused
            currentTime = duration
            // Nothing further will play — release the session so other apps' audio can come back.
            deactivateAudioSession()
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

    /// Request (or release) compact mode for a given reason token. `isCompactMode` is true while ANY
    /// reason is active. A stable per-screen token makes appear/disappear and focus on/off idempotent
    /// and safe under nested presentation (e.g. asset → asset pushes).
    func setCompactMode(_ active: Bool, reason: String) {
        let was = !compactReasons.isEmpty
        if active { compactReasons.insert(reason) } else { compactReasons.remove(reason) }
        let now = !compactReasons.isEmpty
        guard now != was else { return }  // no-op: don't emit a redundant animation
        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
            isCompactMode = now
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
