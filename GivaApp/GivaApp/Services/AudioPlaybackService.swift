// AudioPlaybackService.swift - Manages audio playback for voice mode.
//
// Recording has been moved to VoiceRecordingService (AVAudioEngine-based,
// with two-tier silence detection and progressive chunk transcription).

import AVFoundation
import Foundation

private let log = Log.make(category: "Audio")

@MainActor
class AudioPlaybackService: ObservableObject {
    @Published var isPlaying: Bool = false

    private var audioPlayer: AVAudioPlayer?
    private var audioQueue: [Data] = []
    private var isProcessingQueue = false

    // MARK: - Playback

    /// Enqueue a base64-encoded WAV audio chunk for playback.
    func enqueueAudioChunk(_ base64Data: String) {
        guard let data = Data(base64Encoded: base64Data) else {
            log.warning(" Failed to decode base64 audio data")
            return
        }
        audioQueue.append(data)
        processQueue()
    }

    /// Stop all playback and clear the queue.
    func stopPlayback() {
        audioPlayer?.stop()
        audioPlayer = nil
        audioQueue.removeAll()
        isPlaying = false
        isProcessingQueue = false
    }

    private func processQueue() {
        guard !isProcessingQueue, !audioQueue.isEmpty else { return }
        isProcessingQueue = true
        playNext()
    }

    private func playNext() {
        guard !audioQueue.isEmpty else {
            isProcessingQueue = false
            isPlaying = false
            return
        }

        let data = audioQueue.removeFirst()
        do {
            audioPlayer = try AVAudioPlayer(data: data)
            audioPlayer?.delegate = AudioPlayerDelegateWrapper { [weak self] in
                Task { @MainActor in
                    self?.playNext()
                }
            }
            audioPlayer?.play()
            isPlaying = true
        } catch {
            log.warning(" Playback error: \(error)")
            playNext() // Skip to next chunk
        }
    }
}

// MARK: - AVAudioPlayer Delegate Wrapper

private class AudioPlayerDelegateWrapper: NSObject, AVAudioPlayerDelegate {
    let onFinish: () -> Void

    init(onFinish: @escaping () -> Void) {
        self.onFinish = onFinish
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        onFinish()
    }
}
