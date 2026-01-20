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

    override init() {
        super.init()
        setupAudioSession()
    }

    private func setupAudioSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)
        } catch {
            print("Failed to setup audio session: \(error)")
        }
    }

    /// Play audio data (WAV format).
    func play(audioData: Data) {
        // Add to queue
        audioQueue.append(audioData)

        // Process queue if not already processing
        if !isProcessingQueue {
            processQueue()
        }
    }

    private func processQueue() {
        guard !audioQueue.isEmpty else {
            isProcessingQueue = false
            return
        }

        isProcessingQueue = true
        let audioData = audioQueue.removeFirst()

        do {
            audioPlayer = try AVAudioPlayer(data: audioData)
            audioPlayer?.delegate = self
            audioPlayer?.play()
            isPlaying = true
            print("Playing audio (\(audioData.count) bytes)")
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
