// GivaViewModel.swift - Central state management for the Giva menu bar app.

import SwiftUI

enum AppTab: String, CaseIterable {
    case chat = "Chat"
    case tasks = "Tasks"
}

@MainActor
class GivaViewModel: ObservableObject {
    // Server
    @Published var serverManager = ServerManager()
    private var apiService: APIService?

    // Chat
    @Published var messages: [ChatMessage] = []
    @Published var currentInput: String = ""
    @Published var isStreaming: Bool = false

    // Voice
    @Published var isVoiceEnabled: Bool = false
    @Published var isRecording: Bool = false
    let audioService = AudioPlaybackService()

    // Tasks
    @Published var tasks: [TaskItem] = []
    @Published var isLoadingTasks: Bool = false

    // Status & Profile
    @Published var status: StatusResponse?
    @Published var profile: ProfileResponse?

    // Onboarding
    @Published var isOnboarding: Bool = false
    @Published var onboardingCompleted: Bool = false

    // Reset
    @Published var isResetting: Bool = false

    // Model Setup
    @Published var isModelSetupNeeded: Bool = false
    @Published var isSettingUpModels: Bool = false
    @Published var availableModels: AvailableModelsResponse?
    @Published var downloadProgress: [String: Double] = [:]
    @Published var isDownloadingModels: Bool = false
    @Published var modelSetupError: String?

    // UI
    @Published var currentTab: AppTab = .chat
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var isUpgrading: Bool = false

    // Reference to bootstrap manager (set from GivaApp)
    weak var bootstrapManager: BootstrapManager?

    // Active streaming task (for cancellation)
    private var streamTask: Task<Void, Never>?

    /// Connect to the daemon-managed server (called after bootstrap completes).
    func connectToServer() async {
        await serverManager.connectToDaemon()
        if serverManager.isRunning {
            apiService = APIService(baseURL: serverManager.baseURL)
            await refreshStatus()
            await checkModelSetup()
            await loadProfile()
            await checkOnboarding()
        }
    }

    // MARK: - Chat

    func sendMessage() {
        let query = currentInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, !isStreaming else { return }

        currentInput = ""
        errorMessage = nil

        // Route through onboarding if active
        if isOnboarding {
            sendOnboardingResponse(query)
            return
        }

        // Add user message
        messages.append(ChatMessage(role: "user", content: query))

        // Add placeholder assistant message
        messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isStreaming = true

        let useVoice = isVoiceEnabled

        streamTask = Task {
            guard let api = apiService else {
                appendSystemMessage("Server is not running. Please wait for startup.")
                isStreaming = false
                return
            }
            do {
                let stream = api.streamChat(query: query, voice: useVoice)
                for try await event in stream {
                    if event.event == "thinking" {
                        appendThinkingToLastMessage(event.data)
                    } else if event.event == "token" {
                        finishThinkingIfNeeded()
                        appendToLastMessage(event.data)
                    } else if event.event == "audio_chunk" {
                        audioService.enqueueAudioChunk(event.data)
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // User cancelled
            } catch {
                errorMessage = error.localizedDescription
            }

            finalizeLastMessage()
            isStreaming = false
        }
    }

    func cancelStreaming() {
        streamTask?.cancel()
        streamTask = nil
        audioService.stopPlayback()
        finalizeLastMessage()
        isStreaming = false
    }

    // MARK: - Voice Input

    func startVoiceInput() {
        guard !isStreaming, !isRecording else { return }
        isRecording = true

        Task {
            guard let audioData = await audioService.recordAudio(duration: 5.0) else {
                isRecording = false
                errorMessage = "Could not record audio. Check microphone permissions."
                return
            }
            isRecording = false

            guard let api = apiService else {
                errorMessage = "Server is not running."
                return
            }

            do {
                let text = try await api.transcribe(audioData: audioData)
                if !text.isEmpty {
                    currentInput = text
                    sendMessage()
                } else {
                    errorMessage = "No speech detected. Try again."
                }
            } catch {
                errorMessage = "Transcription error: \(error.localizedDescription)"
            }
        }
    }

    // MARK: - Quick Actions

    func triggerSync() async {
        guard let api = apiService else { return }
        isLoading = true
        errorMessage = nil
        do {
            let result = try await api.triggerSync()
            appendSystemMessage(
                "Sync complete: \(result.mailSynced) emails synced, "
                + "\(result.mailFiltered) filtered, "
                + "\(result.eventsSynced) events synced."
            )
            await refreshStatus()
            // Auto-trigger onboarding if needed
            if result.needsOnboarding {
                startOnboarding()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func triggerExtract() async {
        guard let api = apiService else { return }
        isLoading = true
        errorMessage = nil
        do {
            let result = try await api.triggerExtract()
            appendSystemMessage("Extracted \(result.tasksExtracted) new task(s).")
            await loadTasks()
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func triggerSuggest() {
        guard !isStreaming, apiService != nil else { return }
        errorMessage = nil

        messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isStreaming = true

        streamTask = Task {
            guard let api = apiService else { return }
            do {
                let stream = api.streamSuggest()
                for try await event in stream {
                    if event.event == "thinking" {
                        appendThinkingToLastMessage(event.data)
                    } else if event.event == "token" {
                        finishThinkingIfNeeded()
                        appendToLastMessage(event.data)
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            finalizeLastMessage()
            isStreaming = false
        }
    }

    // MARK: - Onboarding

    func checkOnboarding() async {
        guard let api = apiService else { return }
        do {
            let status = try await api.getOnboardingStatus()
            onboardingCompleted = status.onboardingCompleted
            if status.needsOnboarding {
                startOnboarding()
            }
        } catch {
            // Non-critical
        }
    }

    func startOnboarding() {
        guard !isStreaming, apiService != nil else { return }
        isOnboarding = true
        errorMessage = nil

        // Add system message announcing onboarding
        appendSystemMessage("Let me ask you a few questions to personalize your experience.")

        // Add placeholder for the first question
        messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isStreaming = true

        streamTask = Task {
            guard let api = apiService else { return }
            do {
                let stream = api.streamOnboardingStart()
                for try await event in stream {
                    if event.event == "thinking" {
                        appendThinkingToLastMessage(event.data)
                    } else if event.event == "token" {
                        finishThinkingIfNeeded()
                        appendToLastMessage(event.data)
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            finalizeLastMessage()
            isStreaming = false
        }
    }

    func sendOnboardingResponse(_ text: String) {
        guard !isStreaming, apiService != nil else { return }

        // Add user message
        messages.append(ChatMessage(role: "user", content: text))

        // Add placeholder for next question
        messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isStreaming = true

        streamTask = Task {
            guard let api = apiService else { return }
            do {
                let stream = api.streamOnboardingRespond(response: text)
                for try await event in stream {
                    if event.event == "thinking" {
                        appendThinkingToLastMessage(event.data)
                    } else if event.event == "token" {
                        finishThinkingIfNeeded()
                        appendToLastMessage(event.data)
                    } else if event.event == "error" {
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // cancelled
            } catch {
                errorMessage = error.localizedDescription
            }
            finalizeLastMessage()
            isStreaming = false

            // Check if onboarding is now complete
            do {
                let status = try await api.getOnboardingStatus()
                if status.onboardingCompleted {
                    isOnboarding = false
                    onboardingCompleted = true
                    appendSystemMessage("Onboarding complete! Your preferences have been saved.")
                    await loadProfile()
                }
            } catch {
                // Non-critical
            }
        }
    }

    // MARK: - Reset (tabula rasa)

    func triggerReset() async {
        guard let bootstrap = bootstrapManager else { return }
        isResetting = true
        errorMessage = nil

        // Step 1: Tell server to wipe DB, caches, and user config
        if let api = apiService {
            do {
                _ = try await api.triggerReset()
            } catch {
                // Server may already be shutting down — continue anyway
            }
        }

        // Step 2: Clear all local state
        messages.removeAll()
        tasks.removeAll()
        isOnboarding = false
        onboardingCompleted = false
        profile = nil
        status = nil
        availableModels = nil
        downloadProgress = [:]
        isDownloadingModels = false
        modelSetupError = nil
        isModelSetupNeeded = false

        // Step 3: Disconnect from server
        apiService = nil
        serverManager.isRunning = false

        // Step 4: Full re-bootstrap (stops daemon, deletes venv, reinstalls)
        // This flips bootstrap.isComplete = false, showing BootstrapView.
        // After bootstrap completes, the .onChange(of: bootstrap.isComplete)
        // observer in GivaApp.swift calls connectToServer() → checkModelSetup()
        // → shows ModelSetupView (since user config was deleted).
        await bootstrap.upgrade()
        isResetting = false
    }

    // MARK: - Model Setup

    func checkModelSetup() async {
        guard let api = apiService else { return }
        do {
            let status = try await api.getModelStatus()
            isModelSetupNeeded = !status.setupCompleted
        } catch {
            // If endpoint fails, assume setup not needed (old server)
            isModelSetupNeeded = false
        }
    }

    func fetchAvailableModels() async {
        guard let api = apiService else { return }
        isSettingUpModels = true
        modelSetupError = nil
        do {
            availableModels = try await api.getAvailableModels()
        } catch {
            modelSetupError = "Could not fetch models: \(error.localizedDescription)"
        }
        isSettingUpModels = false
    }

    func selectAndDownloadModels(assistant: String, filter: String) {
        guard let api = apiService else { return }
        isDownloadingModels = true
        modelSetupError = nil
        downloadProgress = [:]

        Task {
            // Step 1: Save model choices
            do {
                _ = try await api.selectModels(assistant: assistant, filter: filter)
            } catch {
                modelSetupError = "Failed to save model selection: \(error.localizedDescription)"
                isDownloadingModels = false
                return
            }

            // Step 2: Download models that need downloading
            let modelsToDownload = Set([assistant, filter])
            for modelId in modelsToDownload {
                downloadProgress[modelId] = 0.0
                do {
                    let stream = api.streamModelDownload(modelId: modelId)
                    for try await event in stream {
                        if event.event == "progress" {
                            if let data = event.data.data(using: .utf8),
                               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                               let percent = json["percent"] as? Double {
                                downloadProgress[modelId] = percent
                            }
                        } else if event.event == "error" {
                            modelSetupError = "Download error: \(event.data)"
                        }
                    }
                    downloadProgress[modelId] = 100.0
                } catch {
                    modelSetupError = "Download failed for \(modelId): \(error.localizedDescription)"
                    isDownloadingModels = false
                    return
                }
            }

            isDownloadingModels = false
            isModelSetupNeeded = false

            // After model setup, trigger initial sync which auto-starts
            // onboarding when needed (via result.needsOnboarding)
            await triggerSync()
        }
    }

    func skipModelSetup() {
        isModelSetupNeeded = false
        // Even when skipping, trigger sync so onboarding can start
        Task { await triggerSync() }
    }

    // MARK: - Upgrade

    func triggerUpgrade() {
        guard let bootstrap = bootstrapManager, !isUpgrading else { return }
        isUpgrading = true
        apiService = nil
        serverManager.isRunning = false

        Task {
            await bootstrap.upgrade()
            isUpgrading = false
            // After upgrade completes, reconnect
            if bootstrap.isComplete {
                await connectToServer()
            }
        }
    }

    // MARK: - Tasks

    func loadTasks() async {
        guard let api = apiService else { return }
        isLoadingTasks = true
        do {
            let response = try await api.getTasks(status: "pending")
            tasks = response.tasks
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoadingTasks = false
    }

    func updateTaskStatus(taskId: Int, status: String) async {
        guard let api = apiService else { return }
        do {
            _ = try await api.updateTaskStatus(taskId: taskId, status: status)
            await loadTasks()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: - Status & Profile

    func refreshStatus() async {
        guard let api = apiService else { return }
        do {
            status = try await api.getStatus()
        } catch {
            // Non-critical
        }
    }

    func loadProfile() async {
        guard let api = apiService else { return }
        do {
            profile = try await api.getProfile()
        } catch {
            // Profile may not exist yet
        }
    }

    // MARK: - Open CLI

    func openCLI() {
        let script = """
        tell application "Terminal"
            activate
            do script "giva"
        end tell
        """
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        try? process.run()
    }

    // MARK: - Helpers

    private func appendToLastMessage(_ text: String) {
        guard !messages.isEmpty else { return }
        messages[messages.count - 1].content += text
    }

    private func appendThinkingToLastMessage(_ text: String) {
        guard !messages.isEmpty else { return }
        messages[messages.count - 1].thinkingContent += text
        messages[messages.count - 1].isThinking = true
    }

    private func finishThinkingIfNeeded() {
        guard !messages.isEmpty else { return }
        if messages[messages.count - 1].isThinking {
            messages[messages.count - 1].isThinking = false
        }
    }

    private func finalizeLastMessage() {
        guard !messages.isEmpty else { return }
        messages[messages.count - 1].isStreaming = false
        messages[messages.count - 1].isThinking = false
    }

    private func appendSystemMessage(_ text: String) {
        messages.append(ChatMessage(role: "assistant", content: text))
    }
}
