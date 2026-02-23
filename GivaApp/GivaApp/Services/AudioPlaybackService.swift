// AudioPlaybackService.swift - Manages audio playback and recording for voice mode.

import AVFoundation
import Foundation

private let log = Log.make(category: "Audio")

@MainActor
class AudioPlaybackService: ObservableObject {
    @Published var isPlaying: Bool = false
    @Published var isRecording: Bool = false

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

    // MARK: - Recording

    enum RecordingError: Error {
        case permissionDenied
        case recordingFailed
    }

    /// Record audio from the microphone.
    /// Throws `RecordingError.permissionDenied` if mic access is denied (caller should open Settings).
    /// Returns the recorded audio data as WAV, or nil if recording produced no data.
    func recordAudio(duration: TimeInterval = 5.0) async throws -> Data? {
        // Request microphone permission
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        switch status {
        case .notDetermined:
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            if !granted { throw RecordingError.permissionDenied }
        case .denied, .restricted:
            throw RecordingError.permissionDenied
        case .authorized:
            break
        @unknown default:
            throw RecordingError.permissionDenied
        }

        return await withCheckedContinuation { continuation in
            let recorder = SimpleAudioRecorder()
            isRecording = true
            recorder.record(duration: duration) { [weak self] data in
                Task { @MainActor in
                    self?.isRecording = false
                    continuation.resume(returning: data)
                }
            }
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

// MARK: - Simple Audio Recorder

private class SimpleAudioRecorder {
    private var audioRecorder: AVAudioRecorder?
    private var tempURL: URL?

    func record(duration: TimeInterval, completion: @escaping (Data?) -> Void) {
        let tempDir = FileManager.default.temporaryDirectory
        let url = tempDir.appendingPathComponent("giva_recording_\(UUID().uuidString).wav")
        tempURL = url

        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]

        do {
            audioRecorder = try AVAudioRecorder(url: url, settings: settings)
            audioRecorder?.record(forDuration: duration)

            // Wait for recording to complete
            DispatchQueue.main.asyncAfter(deadline: .now() + duration + 0.1) { [weak self] in
                self?.audioRecorder?.stop()
                let data = try? Data(contentsOf: url)
                try? FileManager.default.removeItem(at: url)
                completion(data)
            }
        } catch {
            log.error("Recording error: \(error)")
            completion(nil)
        }
    }
}
