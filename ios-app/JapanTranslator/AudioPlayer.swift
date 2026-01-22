import Foundation
import AVFoundation
import Combine

/// Handles audio playback for translated Japanese speech.
@MainActor
class AudioPlayer: NSObject, ObservableObject {
    /// Whether currently playing audio
    @Published var isPlaying: Bool = false

    private var audioPlayer: AVAudioPlayer?
    private var audioQueue: [Data] = []
    private var isProcessingQueue: Bool = false

    /// Last played audio for replay functionality
    private var lastPlayedAudio: Data?

    override init() {
        super.init()
        setupAudioSession()
    }

    private func setupAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            // Use playback category with speaker override for maximum volume
            try session.setCategory(.playback, mode: .default, options: [])
            try session.overrideOutputAudioPort(.speaker)
            try session.setActive(true)
        } catch {
            print("Failed to setup audio session: \(error)")
        }
    }

    /// Ensure audio plays through the loudspeaker at full volume
    private func ensureLoudspeaker() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.overrideOutputAudioPort(.speaker)
        } catch {
            print("Failed to set speaker output: \(error)")
        }
    }

    /// Play audio data (WAV format).
    func play(audioData: Data) {
        // Store for replay
        lastPlayedAudio = audioData

        // Add to queue
        audioQueue.append(audioData)

        // Process queue if not already processing
        if !isProcessingQueue {
            processQueue()
        }
    }

    /// Replay the last played audio
    func replay() {
        guard let audioData = lastPlayedAudio else {
            print("No audio to replay")
            return
        }
        play(audioData: audioData)
    }

    /// Check if there's audio available to replay
    var canReplay: Bool {
        return lastPlayedAudio != nil
    }

    private func processQueue() {
        guard !audioQueue.isEmpty else {
            isProcessingQueue = false
            return
        }

        isProcessingQueue = true
        let audioData = audioQueue.removeFirst()

        // Ensure loudspeaker before playing
        ensureLoudspeaker()

        do {
            audioPlayer = try AVAudioPlayer(data: audioData)
            audioPlayer?.delegate = self
            audioPlayer?.volume = 1.0  // Maximum volume
            audioPlayer?.play()
            isPlaying = true
            print("Playing audio (\(audioData.count) bytes) on speaker")
        } catch {
            print("Failed to play audio: \(error)")
            // Try next in queue
            processQueue()
        }
    }

    /// Stop playback and clear the queue.
    func stop() {
        audioPlayer?.stop()
        audioPlayer = nil
        audioQueue.removeAll()
        isPlaying = false
        isProcessingQueue = false
    }

    /// Clear the audio queue without stopping current playback.
    func clearQueue() {
        audioQueue.removeAll()
    }
}

extension AudioPlayer: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.isPlaying = false

            // Play next in queue
            if !self.audioQueue.isEmpty {
                self.processQueue()
            } else {
                self.isProcessingQueue = false
            }
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        if let error = error {
            print("Audio decode error: \(error)")
        }
        Task { @MainActor in
            self.isPlaying = false
            self.processQueue()
        }
    }
}
