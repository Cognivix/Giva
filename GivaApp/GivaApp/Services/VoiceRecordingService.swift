// VoiceRecordingService.swift - AVAudioEngine-based recording with two-tier silence detection
// and progressive chunk-based transcription via SSE.

import AVFoundation
import Foundation

private let log = Log.make(category: "Voice")

/// Error types specific to voice recording.
enum VoiceRecordingError: Error, LocalizedError {
    case permissionDenied
    case engineSetupFailed(String)
    case noSpeechDetected

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Microphone access denied"
        case .engineSetupFailed(let detail):
            return "Audio engine setup failed: \(detail)"
        case .noSpeechDetected:
            return "No speech detected"
        }
    }
}

/// State machine for the recording lifecycle.
enum RecordingState: Equatable {
    case idle
    case recording
    case finishing
}

/// Manages voice recording with two-tier silence detection and progressive SSE transcription.
///
/// **Tier 1** (short pause, ~1s): Snips audio at natural sentence boundaries, uploads the chunk
/// for transcription while recording continues. Transcribed text appears progressively.
///
/// **Tier 2** (long pause, ~5s): Stops recording, uploads the final chunk, waits for all
/// transcriptions to complete, and invokes the `onComplete` callback with the full text.
@MainActor @Observable
class VoiceRecordingService {

    // MARK: - Public State

    /// Current recording state (observed by ViewModel/Views).
    var state: RecordingState = .idle

    /// Current audio input level (0..1 linear scale, for UI level bars).
    var audioLevel: Float = 0

    /// Concatenation of all transcribed chunks so far (updated progressively).
    var currentTranscription: String = ""

    /// Error message, if any.
    var error: String?

    // MARK: - Callbacks

    /// Called when all chunks are transcribed and the final text is ready.
    var onComplete: ((String) -> Void)?

    /// Called when an error occurs.
    var onError: ((String) -> Void)?

    // MARK: - Constants

    private static let sampleRate: Double = 16000
    private static let silenceThreshold: Float = 0.01  // RMS amplitude
    private static let tier1Silence: TimeInterval = 1.0
    private static let tier2Silence: TimeInterval = 5.0
    private static let maxChunkDuration: TimeInterval = 30.0
    private static let minChunkDuration: TimeInterval = 0.5
    private static let maxRecordingDuration: TimeInterval = 300.0  // 5 minutes
    private static let bufferSize: AVAudioFrameCount = 1600  // 100ms at 16kHz

    // MARK: - Internal State

    private var audioEngine: AVAudioEngine?
    private var accumulatedBuffers: [AVAudioPCMBuffer] = []
    private var silenceStart: Date?
    private var hasSpeechInCurrentChunk = false
    private var hasEverDetectedSpeech = false
    private var chunkStartTime: Date?
    private var recordingStartTime: Date?
    private var chunkIndex = 0

    private var pendingTasks: [Int: Task<Void, Never>] = [:]
    private var transcribedChunks: [Int: TranscribedChunk] = [:]

    private var apiService: (any APIServiceProtocol)?
    private var processedBufferCount = 0
    private var inputSampleRate: Double = 16000
    private var inputChannels: Int = 1

    struct TranscribedChunk {
        let index: Int
        var finalText: String?
        var isDone = false
    }

    // MARK: - Public API

    /// Start recording with two-tier silence detection.
    ///
    /// Requires microphone permission. Throws `VoiceRecordingError.permissionDenied` if denied.
    func startRecording(apiService: any APIServiceProtocol) async throws {
        guard state == .idle else { return }

        // Check mic permission
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        switch status {
        case .notDetermined:
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            if !granted { throw VoiceRecordingError.permissionDenied }
        case .denied, .restricted:
            throw VoiceRecordingError.permissionDenied
        case .authorized:
            break
        @unknown default:
            throw VoiceRecordingError.permissionDenied
        }

        self.apiService = apiService

        // Set up audio engine — tap the input node directly in its native format.
        // Format conversion (to 16kHz mono) happens later in buffersToWAVData().
        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)

        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw VoiceRecordingError.engineSetupFailed(
                "Invalid input format: rate=\(inputFormat.sampleRate) ch=\(inputFormat.channelCount)"
            )
        }

        // Store the input sample rate for later downsampling
        self.inputSampleRate = inputFormat.sampleRate
        self.inputChannels = Int(inputFormat.channelCount)

        // Buffer size scaled to input sample rate (~100ms worth of samples)
        let scaledBufferSize = AVAudioFrameCount(inputFormat.sampleRate * 0.1)

        inputNode.installTap(
            onBus: 0,
            bufferSize: scaledBufferSize,
            format: inputFormat
        ) { [weak self] buffer, _ in
            // Audio thread — compute RMS and dispatch to MainActor
            let rms = Self.computeRMS(buffer)
            let bufferCopy = Self.copyBuffer(buffer)

            Task { @MainActor [weak self] in
                self?.processBuffer(bufferCopy, rms: rms)
            }
        }

        log.info("Audio engine setup: inputFormat=\(inputFormat) sampleRate=\(inputFormat.sampleRate) ch=\(inputFormat.channelCount)")

        do {
            try engine.start()
            log.info("Audio engine started successfully, isRunning=\(engine.isRunning)")
        } catch {
            throw VoiceRecordingError.engineSetupFailed(error.localizedDescription)
        }

        self.audioEngine = engine
        self.state = .recording
        self.chunkStartTime = Date()
        self.recordingStartTime = Date()
        self.chunkIndex = 0
        self.accumulatedBuffers = []
        self.transcribedChunks = [:]
        self.pendingTasks = [:]
        self.currentTranscription = ""
        self.error = nil
        self.hasSpeechInCurrentChunk = false
        self.silenceStart = nil

        log.info("Voice recording started (two-tier silence detection)")
    }

    /// Cancel recording and discard all chunks.
    func cancel() {
        log.info("Voice recording cancelled")
        stopEngine()
        cancelAllPendingTasks()
        resetState()
    }

    // MARK: - Audio Processing (called on MainActor from tap callback)

    private func processBuffer(_ buffer: AVAudioPCMBuffer, rms: Float) {
        guard state == .recording else {
            log.debug("processBuffer: skipped (state=\(String(describing: state)))")
            return
        }

        processedBufferCount += 1
        if processedBufferCount == 1 || processedBufferCount % 100 == 0 {
            log.info("processBuffer #\(processedBufferCount): rms=\(String(format: "%.4f", rms)) speech=\(hasSpeechInCurrentChunk) silence=\(silenceStart != nil) buffers=\(accumulatedBuffers.count)")
        }

        // Update UI audio level (smoothed)
        audioLevel = min(1.0, rms / 0.1)  // Scale: 0.1 RMS → 1.0 level

        accumulatedBuffers.append(buffer)

        let now = Date()

        if rms > Self.silenceThreshold {
            hasSpeechInCurrentChunk = true
            hasEverDetectedSpeech = true
            silenceStart = nil
        } else {
            if silenceStart == nil {
                silenceStart = now
            }

            if let silStart = silenceStart {
                let silenceDuration = now.timeIntervalSince(silStart)
                let chunkDuration = now.timeIntervalSince(chunkStartTime ?? now)

                // Tier 2: long silence after any speech was ever detected → stop recording
                if silenceDuration >= Self.tier2Silence && hasEverDetectedSpeech {
                    triggerTier2()
                    return
                }

                // Tier 1: short silence within a chunk that has speech → snip and continue
                if hasSpeechInCurrentChunk
                    && silenceDuration >= Self.tier1Silence
                    && chunkDuration >= Self.minChunkDuration {
                    triggerTier1()
                    return
                }
            }
        }

        // Force snip on max chunk duration
        if let chunkStart = chunkStartTime,
           now.timeIntervalSince(chunkStart) >= Self.maxChunkDuration,
           hasSpeechInCurrentChunk {
            triggerTier1()
            return
        }

        // Force stop on max recording duration
        if let recordStart = recordingStartTime,
           now.timeIntervalSince(recordStart) >= Self.maxRecordingDuration {
            log.info("Max recording duration (\(Int(Self.maxRecordingDuration))s) reached")
            triggerTier2()
        }
    }

    // MARK: - Tier 1: Snip + Upload + Keep Recording

    private func triggerTier1() {
        let buffersToSend = accumulatedBuffers
        accumulatedBuffers = []
        hasSpeechInCurrentChunk = false
        silenceStart = nil
        chunkStartTime = Date()

        let thisChunkIndex = chunkIndex
        chunkIndex += 1

        log.info("Tier 1 silence: snipping chunk \(thisChunkIndex), continuing recording")

        Task {
            guard let wavData = self.buffersToWAVData(buffersToSend) else {
                log.warning("Failed to convert buffers to WAV for chunk \(thisChunkIndex)")
                return
            }
            await uploadChunk(index: thisChunkIndex, audioData: wavData)
        }
    }

    // MARK: - Tier 2: Stop + Upload Final + Wait

    private func triggerTier2() {
        state = .finishing
        audioLevel = 0

        let buffersToSend = accumulatedBuffers
        accumulatedBuffers = []

        stopEngine()

        let thisChunkIndex = chunkIndex
        chunkIndex += 1

        log.info("Tier 2 silence: stopping recording, uploading final chunk \(thisChunkIndex)")

        Task {
            if hasSpeechInCurrentChunk, let wavData = self.buffersToWAVData(buffersToSend) {
                await uploadChunk(index: thisChunkIndex, audioData: wavData)
            }
            await waitForAllChunksAndFinish()
        }
    }

    // MARK: - Chunk Upload + SSE Consumption

    private func uploadChunk(index: Int, audioData: Data) async {
        guard let api = apiService else { return }

        transcribedChunks[index] = TranscribedChunk(index: index)

        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let stream = api.streamTranscribe(
                    audioData: audioData,
                    filename: "chunk_\(index).wav",
                    chunkId: String(index)
                )
                for try await event in stream {
                    switch event.event {
                    case "final":
                        if let data = event.data.data(using: .utf8),
                           let json = try? JSONDecoder().decode(TranscribeFinalEvent.self, from: data) {
                            self.transcribedChunks[index]?.finalText = json.text
                            self.transcribedChunks[index]?.isDone = true
                            self.rebuildCurrentTranscription()
                        }
                    case "error":
                        log.warning("Chunk \(index) transcription error: \(event.data)")
                        self.transcribedChunks[index]?.isDone = true
                    case "done":
                        self.transcribedChunks[index]?.isDone = true
                    default:
                        break
                    }
                }
            } catch {
                log.warning("Chunk \(index) SSE stream error: \(error.localizedDescription)")
                self.transcribedChunks[index]?.isDone = true
            }
        }

        pendingTasks[index] = task
    }

    private func rebuildCurrentTranscription() {
        currentTranscription = transcribedChunks
            .sorted(by: { $0.key < $1.key })
            .compactMap { $0.value.finalText }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespaces)
    }

    private func waitForAllChunksAndFinish() async {
        // Wait for all pending tasks to complete
        for (_, task) in pendingTasks {
            await task.value
        }

        // Build final text
        let finalText = transcribedChunks
            .sorted(by: { $0.key < $1.key })
            .compactMap { $0.value.finalText }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespaces)

        if finalText.isEmpty {
            log.info("No speech transcribed after all chunks processed")
            onError?("No speech detected. Try again.")
        } else {
            log.info("Voice recording complete: \(transcribedChunks.count) chunks, text: \(String(finalText.prefix(80)))")
            onComplete?(finalText)
        }

        resetState()
    }

    // MARK: - Engine Management

    private func stopEngine() {
        if let engine = audioEngine {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            audioEngine = nil
        }
    }

    private func cancelAllPendingTasks() {
        for (_, task) in pendingTasks {
            task.cancel()
        }
        pendingTasks = [:]
    }

    private func resetState() {
        state = .idle
        audioLevel = 0
        accumulatedBuffers = []
        silenceStart = nil
        hasSpeechInCurrentChunk = false
        hasEverDetectedSpeech = false
        chunkStartTime = nil
        recordingStartTime = nil
        chunkIndex = 0
        pendingTasks = [:]
        transcribedChunks = [:]
        apiService = nil
        processedBufferCount = 0
        inputSampleRate = 16000
        inputChannels = 1
    }

    // MARK: - Static Helpers

    /// Compute RMS (root-mean-square) amplitude of an audio buffer.
    private static func computeRMS(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let channelData = buffer.floatChannelData?[0] else { return 0 }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else { return 0 }

        var sumSquares: Float = 0
        for i in 0..<frameLength {
            let sample = channelData[i]
            sumSquares += sample * sample
        }
        return sqrt(sumSquares / Float(frameLength))
    }

    /// Create a copy of an AVAudioPCMBuffer (the engine reuses buffer memory).
    private static func copyBuffer(_ source: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        guard let copy = AVAudioPCMBuffer(
            pcmFormat: source.format,
            frameCapacity: source.frameLength
        ) else {
            return source  // Fallback: return original (risky but better than crash)
        }
        copy.frameLength = source.frameLength

        if let srcData = source.floatChannelData, let dstData = copy.floatChannelData {
            for channel in 0..<Int(source.format.channelCount) {
                memcpy(dstData[channel], srcData[channel],
                       Int(source.frameLength) * MemoryLayout<Float>.size)
            }
        }
        return copy
    }

    /// Convert accumulated float32 PCM buffers (in native mic format) to WAV Data
    /// (16-bit PCM, 16kHz, mono). Downsamples from `inputSampleRate` if needed.
    private func buffersToWAVData(_ buffers: [AVAudioPCMBuffer]) -> Data? {
        // First pass: extract channel-0 float samples from all buffers
        var allSamples = [Float]()
        for buffer in buffers {
            guard let floatData = buffer.floatChannelData?[0] else { continue }
            let count = Int(buffer.frameLength)
            allSamples.append(contentsOf: UnsafeBufferPointer(start: floatData, count: count))
        }
        guard !allSamples.isEmpty else { return nil }

        // Downsample to 16kHz mono via linear interpolation
        let ratio = inputSampleRate / Self.sampleRate  // e.g. 48000/16000 = 3.0
        let outputFrames: Int
        let outputSamples: [Float]

        if abs(ratio - 1.0) < 0.001 {
            // Already 16kHz — no resampling needed
            outputSamples = allSamples
            outputFrames = allSamples.count
        } else {
            outputFrames = Int(Double(allSamples.count) / ratio)
            guard outputFrames > 0 else { return nil }
            var resampled = [Float](repeating: 0, count: outputFrames)
            for i in 0..<outputFrames {
                let srcIndex = Double(i) * ratio
                let idx0 = Int(srcIndex)
                let frac = Float(srcIndex - Double(idx0))
                let s0 = allSamples[min(idx0, allSamples.count - 1)]
                let s1 = allSamples[min(idx0 + 1, allSamples.count - 1)]
                resampled[i] = s0 + frac * (s1 - s0)
            }
            outputSamples = resampled
        }

        let dataSize = outputFrames * 2  // 16-bit = 2 bytes per sample
        var wavData = Data(capacity: 44 + dataSize)

        // WAV header (44 bytes)
        let wavSampleRate: UInt32 = 16000
        let channels: UInt16 = 1
        let bitsPerSample: UInt16 = 16
        let byteRate = wavSampleRate * UInt32(channels) * UInt32(bitsPerSample / 8)
        let blockAlign = channels * (bitsPerSample / 8)

        wavData.append(contentsOf: "RIFF".utf8)
        wavData.append(littleEndian: UInt32(36 + dataSize))
        wavData.append(contentsOf: "WAVE".utf8)
        wavData.append(contentsOf: "fmt ".utf8)
        wavData.append(littleEndian: UInt32(16))        // Subchunk1 size
        wavData.append(littleEndian: UInt16(1))         // PCM format
        wavData.append(littleEndian: channels)
        wavData.append(littleEndian: wavSampleRate)
        wavData.append(littleEndian: byteRate)
        wavData.append(littleEndian: blockAlign)
        wavData.append(littleEndian: bitsPerSample)
        wavData.append(contentsOf: "data".utf8)
        wavData.append(littleEndian: UInt32(dataSize))

        // PCM samples (float32 → int16)
        for sample in outputSamples {
            let clamped = max(-1.0, min(1.0, sample))
            var int16 = Int16(clamped * Float(Int16.max))
            withUnsafeBytes(of: &int16) { wavData.append(contentsOf: $0) }
        }

        return wavData
    }
}

// MARK: - SSE Response Models

/// JSON payload of the "final" SSE event from `/api/transcribe/stream`.
private struct TranscribeFinalEvent: Codable {
    let text: String
    let chunk_id: String  // swiftlint:disable:this identifier_name
}

// MARK: - Data Extension for Little-Endian Appending

private extension Data {
    mutating func append(littleEndian value: UInt16) {
        var v = value.littleEndian
        Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) }
    }

    mutating func append(littleEndian value: UInt32) {
        var v = value.littleEndian
        Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) }
    }
}
