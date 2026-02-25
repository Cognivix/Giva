// GivaViewModel.swift - Central state management for the Giva menu bar app.
//
// The ViewModel is a thin display layer. All orchestration (sync, onboarding,
// periodic updates) is driven by the server's state machine. The UI connects
// to /api/session on startup, replays conversation history, and then listens
// to the /api/session/stream SSE for server-pushed events (phase changes,
// sync results, onboarding questions, review notifications, etc.).
//
// --- State machine ---
//
// There is ONE authoritative status: the server's `checkpoint` (delivered
// via the session stream as "phase" events).  The ViewModel mirrors it in
// `serverPhase`.  Every piece of UI derives from `serverPhase` — there are
// no shadow booleans like "isOnboarding".
//
// Transient client-side action flags (isRestarting, isUpgrading, isResetting)
// indicate that a client-initiated operation is in progress; they are NOT
// part of the Markov chain — they overlay it until the action completes and
// the server pushes a new phase.

import AppKit
import Observation
import SwiftUI

private let log = Log.make(category: "Session")

enum AppTab: String, CaseIterable {
    case chat = "Chat"
    case tasks = "Tasks"
}

/// Voice input mode. Dictate places text in the input field for editing;
/// full voice auto-sends after silence and enables TTS responses.
enum VoiceMode: Equatable {
    case none
    case dictate
    case fullVoice
}

/// Live sync progress pushed by the server.
struct SyncProgress {
    var stage: String = "emails"  // emails | events | profile
    var mailSynced: Int = 0
    var mailFiltered: Int = 0
    var mailTotal: Int = 0
    var eventsSynced: Int = 0
    var eventsTotal: Int = 0

    /// Human-readable summary, e.g. "40/400 emails, 12 events"
    var displayText: String {
        switch stage {
        case "emails":
            let processed = mailSynced + mailFiltered
            if mailTotal > 0 {
                return "Syncing emails (\(processed)/\(mailTotal))..."
            }
            return "Syncing emails..."
        case "events":
            let mailDone = mailSynced + mailFiltered
            if mailDone > 0 {
                return "Synced \(mailSynced) emails (\(mailFiltered) filtered). Syncing calendar..."
            }
            return "Syncing calendar..."
        case "profile":
            return "Synced \(mailSynced) emails, \(eventsSynced) events. Building profile..."
        default:
            return "Syncing..."
        }
    }
}

@MainActor @Observable
class GivaViewModel {
    // Server — apiService is provided by BootstrapManager once server is reachable
    var serverManager = ServerManager()
    var apiService: (any APIServiceProtocol)?

    // ─── Single authoritative state ───
    var serverPhase: ServerPhase = .unknown

    /// Live sync progress (populated during syncing phase)
    var syncProgress: SyncProgress?

    // ─── Computed properties derived from serverPhase ───

    var isOnboarding: Bool { serverPhase == .onboarding }

    var isOperational: Bool { serverPhase == .operational }

    var isSyncing: Bool { serverPhase == .syncing || isSyncingManual }

    var isSystemBusy: Bool { isRestarting || isResetting || isUpgrading }

    var isChatEnabled: Bool {
        (serverPhase == .operational || serverPhase == .onboarding)
        && !isSystemBusy
    }

    var areActionsEnabled: Bool {
        serverPhase == .operational && !isSystemBusy
    }

    // Chat
    var messages: [ChatMessage] = []
    var currentInput: String = ""
    var isStreaming: Bool = false
    var isLoadingModel: Bool = false

    // Conversation history (date-grouped past chats)
    var conversationDates: [ConversationDate] = []

    // Voice
    var voiceMode: VoiceMode = .none
    var isRecording: Bool = false
    let audioService = AudioPlaybackService()
    var voiceService: VoiceRecordingService?

    // Tasks
    var tasks: [TaskItem] = []
    var isLoadingTasks: Bool = false

    // Status & Profile
    var status: StatusResponse?
    var profile: ProfileResponse?

    // Goals
    var isDailyReviewDue: Bool = false
    var goalsViewModel: GoalsViewModel?

    // Transient action flags (client-side only, not part of the Markov chain)
    var isRestarting: Bool = false
    var isResetting: Bool = false
    var isUpgrading: Bool = false

    /// Manual sync in progress (user-triggered via Sync button, NOT the initial sync)
    var isSyncingManual: Bool = false

    // Model Setup
    var isModelSetupNeeded: Bool = false
    var isSettingUpModels: Bool = false
    var availableModels: AvailableModelsResponse?
    var downloadProgress: [String: Double] = [:]
    var isDownloadingModels: Bool = false
    var modelSetupError: String?

    // UI
    var currentTab: AppTab = .chat
    var isLoading: Bool = false
    var errorMessage: String?

    /// Whether the main window is currently open (tracked for dock icon + menu bar behavior)
    var isMainWindowOpen: Bool = false

    /// Persisted preference: should the menu bar click open the full window or the popover?
    /// Defaults to `true` (full window on first launch). Only changed by the toggle button.
    private static let lastUsedFullWindowKey = "lastUsedFullWindow"

    var lastUsedFullWindow: Bool {
        get {
            access(keyPath: \.lastUsedFullWindow)
            return UserDefaults.standard.object(forKey: Self.lastUsedFullWindowKey) as? Bool ?? true
        }
        set {
            withMutation(keyPath: \.lastUsedFullWindow) {
                UserDefaults.standard.set(newValue, forKey: Self.lastUsedFullWindowKey)
            }
        }
    }

    // Agent queue state
    var pendingConfirmation: AgentConfirmation?
    var activeJobs: [AgentJobItem] = []

    // Reference to bootstrap manager (set from GivaApp)
    weak var bootstrapManager: BootstrapManager?

    // Active streaming task (for cancellation)
    private var streamTask: Task<Void, Never>?

    // Session stream task (long-lived connection to server)
    private var sessionStreamTask: Task<Void, Never>?

    /// Connect to the server using the bootstrap manager's API service.
    /// Called after bootstrap reports ready (models downloaded).
    /// Fetches session state from server, replays history, and connects to session stream.
    func connectToServer(from bootstrap: BootstrapManager) async {
        guard let api = bootstrap.apiService else { return }
        log.info("connectToServer (phase=\(self.serverPhase))")
        apiService = api
        serverManager.recordHeartbeat()
        goalsViewModel = GoalsViewModel(apiService: api)

        await fetchSessionState()
        await refreshStatus()
        await loadProfile()
        await checkReviewDue()
        await loadConversationDates()
        connectSessionStream()
    }

    /// Attempt to reconnect using the stored bootstrap manager.
    func reconnect() async {
        guard let bootstrap = bootstrapManager else { return }
        await connectToServer(from: bootstrap)
    }

    // MARK: - Session (server-driven state machine)

    /// Fetch session state from server and replay conversation history.
    private func fetchSessionState() async {
        guard let api = apiService else { return }
        do {
            let session = try await api.getSession()
            serverPhase = ServerPhase(serverString: session.phase)
            log.info("fetchSessionState → phase=\(session.phase)")

            switch serverPhase {
            case .syncing:
                if messages.isEmpty {
                    appendSystemMessage("Syncing your emails and calendar...")
                }
            case .onboarding:
                messages.removeAll()
                for msg in session.messages {
                    messages.append(ChatMessage(role: msg.role, content: msg.content))
                }
            default:
                break
            }
        } catch {
            log.error("fetchSessionState failed: \(error.localizedDescription)")
        }
    }

    /// Connect to the long-lived session SSE stream.
    /// The server pushes: phase changes, sync results, onboarding tokens,
    /// review notifications, stats updates, etc.
    /// On reconnect, re-fetches session state to pick up missed messages.
    private func connectSessionStream() {
        sessionStreamTask?.cancel()
        guard let api = apiService else { return }

        sessionStreamTask = Task {
            var isFirstConnect = true
            while !Task.isCancelled {
                if !isFirstConnect {
                    serverManager.markConnecting()
                    await fetchSessionState()
                    await refreshStatus()
                }
                isFirstConnect = false

                do {
                    log.info("SSE stream connecting...")
                    for try await event in api.streamSession() {
                        guard !Task.isCancelled else { return }
                        serverManager.recordHeartbeat()
                        handleSessionEvent(event)
                    }
                    log.warning("SSE stream ended (server closed)")
                    serverManager.recordDisconnect()
                } catch is CancellationError {
                    return
                } catch {
                    log.error("SSE stream error: \(error.localizedDescription)")
                    serverManager.recordDisconnect()
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                }
            }
        }
    }

    /// Handle a server-pushed event from the session stream.
    /// The "phase" event is the ONLY way serverPhase changes.
    private func handleSessionEvent(_ event: SSEEvent) {
        if event.event != "heartbeat" {
            log.debug("event=\(event.event) data=\(event.data.prefix(120))")
        }

        switch event.event {
        case "phase":
            let oldPhase = serverPhase
            serverPhase = ServerPhase(serverString: event.data)
            log.info("phase: \(oldPhase) → \(serverPhase)")

            switch serverPhase {
            case .syncing:
                syncProgress = SyncProgress()
            case .onboarding:
                syncProgress = nil
            case .operational:
                syncProgress = nil
                isSyncingManual = false
                if oldPhase == .onboarding {
                    appendSystemMessage("Onboarding complete! Your preferences have been saved.")
                    Task { await loadProfile() }
                }
                Task {
                    await refreshStatus()
                    await loadTasks()
                }
            default:
                break
            }

        case "sync_progress":
            if let data = event.data.data(using: .utf8),
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                var p = syncProgress ?? SyncProgress()
                p.stage = json["stage"] as? String ?? p.stage
                p.mailSynced = json["mail_synced"] as? Int ?? p.mailSynced
                p.mailFiltered = json["mail_filtered"] as? Int ?? p.mailFiltered
                p.mailTotal = json["mail_total"] as? Int ?? p.mailTotal
                p.eventsSynced = json["events_synced"] as? Int ?? p.eventsSynced
                p.eventsTotal = json["events_total"] as? Int ?? p.eventsTotal
                syncProgress = p
            }

        case "model_loading":
            // Skip if the direct respond/chat stream is handling model loading
            guard streamTask == nil else { break }
            isLoadingModel = true
            // Create a streaming placeholder so the "Loading AI model..." indicator shows
            if messages.isEmpty || messages.last?.role != "assistant" || messages.last?.isStreaming != true {
                messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
                isStreaming = true
            }

        case "onboarding_token":
            // Skip session-stream broadcast if the direct respond stream is active
            // (sendOnboardingResponse handles those tokens directly).
            if streamTask != nil {
                log.debug("session: skipping broadcast onboarding_token (respond active)")
                break
            }
            isLoadingModel = false
            if messages.isEmpty || messages.last?.role != "assistant" || messages.last?.isStreaming != true {
                messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
                isStreaming = true
            }
            appendToLastMessage(event.data)

        case "onboarding_thinking":
            if streamTask != nil { break }
            if messages.isEmpty || messages.last?.role != "assistant" || messages.last?.isStreaming != true {
                messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
                isStreaming = true
            }
            appendThinkingToLastMessage(event.data)

        case "onboarding_done":
            if streamTask != nil {
                log.debug("session: skipping broadcast onboarding_done (respond active)")
                break
            }
            finalizeLastMessage()
            isStreaming = false

        case "onboarding_complete":
            finalizeLastMessage()
            isStreaming = false
            // Server will push "phase: operational" next.

        case "sync_started":
            isSyncingManual = true
            syncProgress = SyncProgress()

        case "sync_complete":
            isSyncingManual = false
            syncProgress = nil
            Task {
                await refreshStatus()
                await loadTasks()
            }

        case "stats":
            Task { await refreshStatus() }

        case "review_due":
            isDailyReviewDue = true
            goalsViewModel?.isDailyReviewDue = true

        case "error":
            errorMessage = event.data

        case "heartbeat":
            break

        // Agent queue events (from background queue consumer)
        case "agent_job_enqueued", "agent_job_confirmed", "agent_job_started",
             "agent_job_completed", "agent_job_failed", "agent_job_cancelled":
            updateAgentQueue(event)

        default:
            break
        }
    }

    // MARK: - Chat

    func sendMessage() {
        let query = currentInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, !isStreaming else { return }

        currentInput = ""
        errorMessage = nil

        // Route through onboarding if active (server-driven)
        if isOnboarding {
            sendOnboardingResponse(query)
            return
        }

        // Add user message
        messages.append(ChatMessage(role: "user", content: query))

        // Add placeholder assistant message
        messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isStreaming = true

        let useVoice = (voiceMode == .fullVoice)

        streamTask = Task {
            guard let api = apiService else {
                appendSystemMessage("Server is not running. Please wait for startup.")
                isStreaming = false
                return
            }
            do {
                let stream = api.streamChat(query: query, voice: useVoice)
                for try await event in stream {
                    if event.event == "model_loading" {
                        isLoadingModel = true
                    } else if event.event == "thinking" {
                        isLoadingModel = false
                        appendThinkingToLastMessage(event.data)
                    } else if event.event == "token" {
                        isLoadingModel = false
                        finishThinkingIfNeeded()
                        appendToLastMessage(event.data)
                    } else if event.event == "audio_chunk" {
                        audioService.enqueueAudioChunk(event.data)
                    } else if event.event == "agent_actions" {
                        handleAgentActions(event.data)
                    } else if event.event == "agent_confirm" {
                        handleAgentConfirmation(event.data)
                    } else if event.event == "agent_queued" {
                        handleAgentQueued(event.data)
                    } else if event.event == "error" {
                        isLoadingModel = false
                        errorMessage = event.data
                    }
                }
            } catch is CancellationError {
                // User cancelled
            } catch {
                errorMessage = error.localizedDescription
            }

            isLoadingModel = false
            finalizeLastMessage()
            isStreaming = false
            streamTask = nil
            voiceMode = .none

            // Refresh conversation dates for sidebar
            await loadConversationDates()
        }
    }

    func cancelStreaming() {
        streamTask?.cancel()
        streamTask = nil
        audioService.stopPlayback()
        finalizeLastMessage()
        isStreaming = false
    }

    // MARK: - Agent Handling

    /// Handle agent_actions events from the chat stream (post-chat agents).
    private func handleAgentActions(_ json: String) {
        for action in AgentActionHandler.parseActions(json) {
            switch action.type {
            case "task_created":
                if let title = action.title { appendSystemMessage("✓ Created task: \(title)") }
                Task { await loadTasks() }
            case "task_completed":
                if let title = action.title { appendSystemMessage("✓ Completed task: \(title)") }
                Task { await loadTasks() }
            case "objective_enriched":
                appendSystemMessage("✓ Objective details saved")
                Task { await goalsViewModel?.loadGoals() }
            case "preference":
                if let key = action.key { appendSystemMessage("✓ Noted preference: \(key)") }
            default:
                break
            }
        }
    }

    /// Handle agent_confirm event — an agent wants user approval before running.
    private func handleAgentConfirmation(_ json: String) {
        guard let confirmation = AgentActionHandler.parseConfirmation(json) else { return }
        pendingConfirmation = confirmation
        messages.append(ChatMessage(role: "system", content: "[AGENT_CONFIRM:\(confirmation.id)]"))
    }

    /// Handle agent_queued event — a non-confirmation agent was queued.
    private func handleAgentQueued(_ json: String) {
        guard let name = AgentActionHandler.parseQueuedAgentName(json) else { return }
        appendSystemMessage("⚡ \(name) is working in the background…")
    }

    /// Approve a pending agent confirmation.
    func approveAgent(jobId: String) {
        pendingConfirmation = nil
        Task {
            do {
                try await apiService?.confirmAgent(jobId: jobId)
                appendSystemMessage("Agent approved — working in background…")
            } catch {
                appendSystemMessage("Failed to approve agent: \(error.localizedDescription)")
            }
        }
    }

    /// Dismiss a pending agent confirmation.
    func dismissAgent(jobId: String) {
        pendingConfirmation = nil
        Task {
            do {
                try await apiService?.cancelAgent(jobId: jobId)
            } catch {
                log.warning("Failed to cancel agent job: \(error)")
            }
        }
    }

    /// Update agent queue state from session stream events.
    private func updateAgentQueue(_ event: SSEEvent) {
        guard let data = event.data.data(using: .utf8),
              let job = try? JSONDecoder().decode(AgentJobItem.self, from: data)
        else { return }

        // Update or insert in activeJobs list
        if let idx = activeJobs.firstIndex(where: { $0.jobId == job.jobId }) {
            if job.isTerminal {
                activeJobs.remove(at: idx)
            } else {
                activeJobs[idx] = job
            }
        } else if !job.isTerminal {
            activeJobs.append(job)
        }

        // Notify chat when a background job completes
        if event.event == "agent_job_completed" {
            if let output = job.result?.output, !output.isEmpty {
                let preview = output.count > 200 ? String(output.prefix(200)) + "…" : output
                appendSystemMessage("✓ Agent finished: \(preview)")
            } else {
                appendSystemMessage("✓ Agent job completed.")
            }
            Task { await loadTasks() }
        } else if event.event == "agent_job_failed" {
            let errMsg = job.error ?? "Unknown error"
            appendSystemMessage("⚠ Agent failed: \(errMsg)")
        }
    }

    /// Refresh agent queue from the server.
    func refreshAgentQueue() async {
        guard let api = apiService else { return }
        do {
            let response = try await api.getAgentQueue()
            activeJobs = response.jobs.filter { !$0.isTerminal }
        } catch {
            log.warning("Failed to refresh agent queue: \(error)")
        }
    }

    /// Request AI assistance for a task via the orchestrator.
    func requestTaskAI(taskId: Int) async {
        guard let api = apiService else { return }
        do {
            let result = try await api.taskAI(taskId: taskId)
            if let jobId = result["job_id"] as? String,
               let planSummary = result["plan_summary"] as? String {
                // The job is enqueued as pending_confirmation — it will arrive
                // via the session SSE stream as an agent_job_enqueued event.
                // Show a summary in chat.
                appendSystemMessage(
                    "✨ AI plan for task ready — check the agent activity panel to approve."
                )
                log.info("Task AI job created: \(jobId)")
                // Refresh queue to show it immediately
                await refreshAgentQueue()
            }
        } catch {
            appendSystemMessage("Failed to create AI plan: \(error.localizedDescription)")
        }
    }

    // MARK: - Voice Input

    func startVoiceInput(mode: VoiceMode) {
        guard mode != .none, !isStreaming, !isRecording else { return }
        guard let api = apiService else {
            errorMessage = "Server is not running."
            return
        }

        voiceMode = mode
        let service = VoiceRecordingService()

        service.onComplete = { [weak self] text in
            guard let self else { return }
            self.currentInput = text
            self.isRecording = false
            self.voiceService = nil
            if mode == .fullVoice {
                // Full voice: auto-send + TTS response
                self.sendMessage()
            }
            // Dictate: text stays in field for editing, user sends manually
        }
        service.onError = { [weak self] error in
            guard let self else { return }
            self.errorMessage = error
            self.isRecording = false
            self.voiceService = nil
            self.voiceMode = .none
        }
        voiceService = service
        isRecording = true

        Task {
            do {
                try await service.startRecording(apiService: api)
            } catch VoiceRecordingError.permissionDenied {
                isRecording = false
                voiceService = nil
                voiceMode = .none
                errorMessage = "Microphone access denied. Opening Privacy Settings…"
                if let url = URL(
                    string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
                ) {
                    NSWorkspace.shared.open(url)
                }
            } catch {
                isRecording = false
                voiceService = nil
                voiceMode = .none
                errorMessage = "Could not start recording: \(error.localizedDescription)"
            }
        }
    }

    func cancelVoiceInput() {
        voiceService?.cancel()
        voiceService = nil
        isRecording = false
        voiceMode = .none
    }

    // MARK: - Quick Actions

    func triggerSync() async {
        guard let api = apiService, !isSyncing else { return }
        isSyncingManual = true
        syncProgress = SyncProgress()
        errorMessage = nil
        do {
            let result = try await api.triggerSync()
            appendSystemMessage(
                "Sync complete: \(result.mailSynced) emails synced, "
                + "\(result.mailFiltered) filtered, "
                + "\(result.eventsSynced) events synced."
            )
            await refreshStatus()
        } catch {
            errorMessage = error.localizedDescription
        }
        isSyncingManual = false
        syncProgress = nil
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

    // MARK: - Onboarding (server-driven, UI just sends responses)

    /// Send a user response during onboarding. The server drives the conversation.
    private func sendOnboardingResponse(_ text: String) {
        guard !isStreaming, apiService != nil else {
            log.warning("sendOnboardingResponse: blocked (isStreaming=\(self.isStreaming) api=\(self.apiService != nil))")
            return
        }

        log.info("sendOnboardingResponse: sending '\(text.prefix(50))'")

        // Add user message
        messages.append(ChatMessage(role: "user", content: text))

        // Add placeholder for next question
        messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isStreaming = true

        streamTask = Task {
            guard let api = apiService else {
                log.error("sendOnboardingResponse: no apiService in task")
                return
            }
            var eventCount = 0
            var tokenCount = 0
            do {
                log.info("sendOnboardingResponse: opening respond stream")
                let stream = api.streamSessionRespond(response: text)
                for try await event in stream {
                    eventCount += 1
                    if event.event == "model_loading" {
                        log.info("respond: model_loading")
                        isLoadingModel = true
                    } else if event.event == "onboarding_thinking" {
                        isLoadingModel = false
                        appendThinkingToLastMessage(event.data)
                    } else if event.event == "onboarding_token" {
                        isLoadingModel = false
                        tokenCount += 1
                        finishThinkingIfNeeded()
                        appendToLastMessage(event.data)
                    } else if event.event == "onboarding_done" {
                        log.info("respond: onboarding_done (tokens=\(tokenCount))")
                    } else if event.event == "onboarding_complete" {
                        log.info("respond: onboarding_complete")
                        break
                    } else if event.event == "error" {
                        log.error("respond: error=\(event.data)")
                        isLoadingModel = false
                        errorMessage = event.data
                    } else {
                        log.info("respond: unhandled event=\(event.event)")
                    }
                }
                log.info("respond: stream ended (events=\(eventCount) tokens=\(tokenCount))")
            } catch is CancellationError {
                log.info("respond: cancelled")
            } catch {
                log.error("respond: error=\(error.localizedDescription)")
                errorMessage = error.localizedDescription
            }

            isLoadingModel = false
            finalizeLastMessage()
            isStreaming = false
            streamTask = nil
            log.info("respond: finalized (msgCount=\(self.messages.count) lastContent=\(self.messages.last?.content.prefix(50) ?? "nil"))")
        }
    }

    // MARK: - Restart (daemon restart, no data loss)

    func triggerRestart() async {
        guard let bootstrap = bootstrapManager, !isRestarting else { return }
        isRestarting = true
        errorMessage = nil

        // 1. Disconnect our streams
        sessionStreamTask?.cancel()
        sessionStreamTask = nil
        streamTask?.cancel()
        streamTask = nil
        serverManager.markOffline()

        // 2. Restart the daemon via launchctl (waits for port to free)
        await bootstrap.restartDaemon()

        // 3. Wait for server to come back
        serverManager.markConnecting()
        let healthy = await serverManager.waitForHealth(timeout: 60)

        if healthy {
            serverManager.recordHeartbeat()
            apiService = bootstrap.apiService ?? APIService(baseURL: serverManager.baseURL)

            // 4. Reconnect session stream + refresh UI
            await fetchSessionState()
            await refreshStatus()
            await loadProfile()
            connectSessionStream()
            appendSystemMessage("Server restarted.")
        } else {
            serverManager.connectionState = .offline
            errorMessage = "Server didn't restart. Check logs."
        }

        isRestarting = false
    }

    // MARK: - Reset (wipe all data, fresh start)

    func triggerReset() async {
        guard let bootstrap = bootstrapManager, !isResetting else { return }
        log.info("Reset: starting")
        isResetting = true
        errorMessage = nil

        // 1. Disconnect streams
        sessionStreamTask?.cancel()
        sessionStreamTask = nil
        streamTask?.cancel()
        streamTask = nil

        // 2. Tell server to wipe DB + roll checkpoint to 'unknown'
        if let api = apiService {
            do {
                _ = try await api.triggerReset()
                log.info("Reset: server API succeeded")
            } catch {
                log.error("Reset: server API failed — \(error.localizedDescription)")
                // Proceed anyway — daemon restart will pick up a clean state
            }
        }

        // 3. Clear local UI state
        messages.removeAll()
        tasks.removeAll()
        serverPhase = .unknown
        syncProgress = nil
        profile = nil
        status = nil
        apiService = nil
        goalsViewModel = nil
        availableModels = nil
        downloadProgress = [:]
        isDownloadingModels = false
        modelSetupError = nil
        isModelSetupNeeded = false
        serverManager.markOffline()

        // 4. Restart daemon and wait for it to come back.
        //    resetAndRestart() re-enters the full bootstrap observation loop
        //    (model check → sync → onboarding → operational).
        await bootstrap.resetAndRestart()

        isResetting = false

        // 5. Reconnect if the server came back ready.
        //    We can't rely on GivaApp.onChange(bootstrap.isReady) because
        //    isReady flipped true while isResetting was still true (blocking
        //    the guard), and onChange won't re-fire once isResetting clears.
        if bootstrap.isReady {
            log.info("Reset: server ready, reconnecting")
            await connectToServer(from: bootstrap)
        } else {
            log.info("Reset: server not ready yet (bootstrap will drive UI)")
        }
    }

    // MARK: - Model Setup

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

    /// Tell the server which models to use.  The server-side bootstrap
    /// picks up from here and downloads them automatically.
    func selectModels(assistant: String, filter: String) {
        guard let api = apiService else { return }
        isDownloadingModels = true
        modelSetupError = nil

        Task {
            do {
                _ = try await api.selectModels(assistant: assistant, filter: filter)
                // Bootstrap SSE stream will report download progress.
                // The BootstrapManager observes it and updates isReady.
            } catch {
                modelSetupError = "Failed to save model selection: \(error.localizedDescription)"
                isDownloadingModels = false
            }
        }
    }

    func skipModelSetup() {
        isModelSetupNeeded = false
        Task { await triggerSync() }
    }

    // MARK: - Upgrade (pip install + daemon restart, data preserved)

    func triggerUpgrade() async {
        guard let bootstrap = bootstrapManager, !isUpgrading else { return }
        log.info("Upgrade starting")
        isUpgrading = true
        errorMessage = nil

        // 1. Disconnect our streams
        sessionStreamTask?.cancel()
        sessionStreamTask = nil
        streamTask?.cancel()
        streamTask = nil
        serverManager.markOffline()

        // 2. Run the upgrade (pip install + daemon restart)
        await bootstrap.triggerUpgrade()
        log.info("Upgrade done (reachable=\(bootstrap.isServerReachable), ready=\(bootstrap.isReady))")

        // 3. Reconnect if server came back
        if bootstrap.isServerReachable {
            let api = bootstrap.apiService ?? APIService(baseURL: serverManager.baseURL)
            apiService = api
            goalsViewModel = GoalsViewModel(apiService: api)
            serverManager.recordHeartbeat()

            // 4. Reconnect session stream (will resume lifecycle if needed)
            await fetchSessionState()
            await refreshStatus()
            await loadProfile()
            connectSessionStream()
            appendSystemMessage("Upgrade complete.")
        } else {
            serverManager.connectionState = .offline
            errorMessage = bootstrap.errorMessage ?? "Server didn't restart after upgrade."
        }

        isUpgrading = false
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

    // MARK: - Conversation History

    func loadConversationDates() async {
        guard let api = apiService else { return }
        do {
            let response = try await api.getConversationDates()
            conversationDates = response.dates
        } catch {
            // Non-critical — sidebar just won't show history
        }
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

    // MARK: - Goals

    func checkReviewDue() async {
        guard let api = apiService else { return }
        do {
            let status = try await api.getReviewStatus()
            isDailyReviewDue = status.due
            goalsViewModel?.isDailyReviewDue = status.due
        } catch {
            // Non-critical
        }
    }

    func openGoalsWindow() {
        NSApp.activate(ignoringOtherApps: true)
        for window in NSApp.windows where window.title == "Goals & Objectives" {
            window.makeKeyAndOrderFront(nil)
            return
        }
        // The Window scene will be created by SwiftUI when openWindow is called
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
