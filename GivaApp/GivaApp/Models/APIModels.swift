// APIModels.swift - Codable structs mirroring the Python API response schemas.

import Foundation

// MARK: - Server Phase

enum ServerPhase: String, Equatable {
    case unknown
    case ready
    case downloadingDefaultModel = "downloading_default_model"
    case awaitingModelSelection = "awaiting_model_selection"
    case downloadingUserModels = "downloading_user_models"
    case validating
    case syncing
    case onboarding
    case operational

    init(serverString: String) {
        self = ServerPhase(rawValue: serverString) ?? .unknown
    }
}

// MARK: - Request Models

struct ChatRequest: Encodable {
    let query: String
    var voice: Bool = false
}

struct TranscribeResponse: Codable {
    let text: String
}

struct UpdateTaskStatusRequest: Encodable {
    let status: String
}

// MARK: - Response Models

struct HealthResponse: Codable {
    let status: String
    let version: String
    let commit: String
}

struct SyncInfoItem: Codable {
    let source: String
    let lastSync: String?
    let lastCount: Int
    let lastStatus: String

    enum CodingKeys: String, CodingKey {
        case source
        case lastSync = "last_sync"
        case lastCount = "last_count"
        case lastStatus = "last_status"
    }
}

struct StatusResponse: Codable {
    let emails: Int
    let events: Int
    let pendingTasks: Int
    let syncs: [SyncInfoItem]
    let model: String
    let modelLoaded: Bool

    enum CodingKeys: String, CodingKey {
        case emails, events, syncs, model
        case pendingTasks = "pending_tasks"
        case modelLoaded = "model_loaded"
    }
}

struct TaskItem: Codable, Identifiable {
    let id: Int
    let title: String
    let description: String
    let sourceType: String
    let sourceId: Int
    let priority: String
    let dueDate: String?
    let status: String
    let classification: String?
    let dismissalReason: String?
    let dismissedAt: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, description, priority, status, classification
        case sourceType = "source_type"
        case sourceId = "source_id"
        case dueDate = "due_date"
        case dismissalReason = "dismissal_reason"
        case dismissedAt = "dismissed_at"
        case createdAt = "created_at"
    }

    var priorityColor: String {
        switch priority {
        case "high": return "red"
        case "medium": return "orange"
        case "low": return "gray"
        default: return "primary"
        }
    }

    var formattedDueDate: String? {
        guard let dueDate = dueDate else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withFullDate]
        if let date = formatter.date(from: String(dueDate.prefix(10))) {
            let display = DateFormatter()
            display.dateFormat = "MMM d"
            return display.string(from: date)
        }
        return String(dueDate.prefix(10))
    }
}

struct TaskListResponse: Codable {
    let tasks: [TaskItem]
    let count: Int
}

struct UpdateTaskStatusResponse: Codable {
    let success: Bool
    let taskId: Int
    let status: String

    enum CodingKeys: String, CodingKey {
        case success, status
        case taskId = "task_id"
    }
}

struct DismissedTaskItem: Codable, Identifiable {
    let id: Int
    let title: String
    let dismissalReason: String
    let dismissedAt: String?
    let sourceType: String
    let priority: String

    enum CodingKeys: String, CodingKey {
        case id, title, priority
        case dismissalReason = "dismissal_reason"
        case dismissedAt = "dismissed_at"
        case sourceType = "source_type"
    }
}

struct DismissedTaskListResponse: Codable {
    let tasks: [DismissedTaskItem]
    let count: Int
}

struct RestoreTaskResponse: Codable {
    let success: Bool
    let taskId: Int

    enum CodingKeys: String, CodingKey {
        case success
        case taskId = "task_id"
    }
}

struct ProfileResponse: Codable {
    let displayName: String
    let emailAddress: String
    let topTopics: [String]
    let avgResponseTimeMin: Double
    let emailVolumeDaily: Double
    let summary: String
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case summary
        case displayName = "display_name"
        case emailAddress = "email_address"
        case topTopics = "top_topics"
        case avgResponseTimeMin = "avg_response_time_min"
        case emailVolumeDaily = "email_volume_daily"
        case updatedAt = "updated_at"
    }
}

struct SyncResponse: Codable {
    let mailSynced: Int
    let mailFiltered: Int
    let eventsSynced: Int
    let profileUpdated: Bool
    let needsOnboarding: Bool

    enum CodingKeys: String, CodingKey {
        case mailSynced = "mail_synced"
        case mailFiltered = "mail_filtered"
        case eventsSynced = "events_synced"
        case profileUpdated = "profile_updated"
        case needsOnboarding = "needs_onboarding"
    }
}

struct OnboardingStatusResponse: Codable {
    let needsOnboarding: Bool
    let onboardingStep: Int
    let onboardingCompleted: Bool

    enum CodingKeys: String, CodingKey {
        case needsOnboarding = "needs_onboarding"
        case onboardingStep = "onboarding_step"
        case onboardingCompleted = "onboarding_completed"
    }
}

struct OnboardingRequest: Encodable {
    let response: String
}

struct ResetResponse: Codable {
    let success: Bool
    let message: String
}

struct ExtractResponse: Codable {
    let tasksExtracted: Int

    enum CodingKeys: String, CodingKey {
        case tasksExtracted = "tasks_extracted"
    }
}

struct ErrorResponse: Codable {
    let detail: String
}

// MARK: - Model Management

struct HardwareInfo: Codable {
    let chip: String
    let ramGb: Int
    let gpuCores: Int

    enum CodingKeys: String, CodingKey {
        case chip
        case ramGb = "ram_gb"
        case gpuCores = "gpu_cores"
    }
}

struct ModelInfo: Codable, Identifiable {
    var id: String { modelId }

    let modelId: String
    let sizeGb: Double
    let params: String
    let quant: String
    let downloads: Int
    let isDownloaded: Bool
    let downloadStatus: String  // complete | partial | not_downloaded

    enum CodingKeys: String, CodingKey {
        case modelId = "model_id"
        case sizeGb = "size_gb"
        case isDownloaded = "is_downloaded"
        case downloadStatus = "download_status"
        case params, quant, downloads
    }

    /// Human-readable display name (strip "mlx-community/" prefix)
    var displayName: String {
        modelId.replacingOccurrences(of: "mlx-community/", with: "")
    }

    /// True if download was interrupted and can be resumed
    var isPartiallyDownloaded: Bool {
        downloadStatus == "partial"
    }

    /// Formatted size string
    var sizeString: String {
        if sizeGb >= 1.0 {
            return String(format: "%.1f GB", sizeGb)
        }
        return String(format: "%.0f MB", sizeGb * 1024)
    }

    /// Formatted download count
    var downloadsString: String {
        if downloads >= 1_000_000 {
            return String(format: "%.1fM", Double(downloads) / 1_000_000)
        }
        if downloads >= 1_000 {
            return String(format: "%.1fK", Double(downloads) / 1_000)
        }
        return "\(downloads)"
    }
}

struct VlmRecommendation: Codable {
    let vlmModel: String
    let reasoning: String

    enum CodingKeys: String, CodingKey {
        case vlmModel = "vlm_model"
        case reasoning
    }
}

struct ModelRecommendation: Codable {
    let assistant: String
    let filter: String
    let reasoning: String
    let vlm: VlmRecommendation?
}

struct ModelStatusResponse: Codable {
    let setupCompleted: Bool
    let currentAssistant: String
    let currentFilter: String
    let currentVlm: String?
    let vlmEnabled: Bool?
    let hardware: HardwareInfo

    enum CodingKeys: String, CodingKey {
        case setupCompleted = "setup_completed"
        case currentAssistant = "current_assistant"
        case currentFilter = "current_filter"
        case currentVlm = "current_vlm"
        case vlmEnabled = "vlm_enabled"
        case hardware
    }
}

struct AvailableModelsResponse: Codable {
    let hardware: HardwareInfo
    let compatibleModels: [ModelInfo]
    let recommended: ModelRecommendation
    let vlmModels: [ModelInfo]?

    enum CodingKeys: String, CodingKey {
        case hardware
        case compatibleModels = "compatible_models"
        case recommended
        case vlmModels = "vlm_models"
    }
}

struct ModelSelectRequest: Encodable {
    let assistantModel: String
    let filterModel: String
    var vlmModel: String = ""

    enum CodingKeys: String, CodingKey {
        case assistantModel = "assistant_model"
        case filterModel = "filter_model"
        case vlmModel = "vlm_model"
    }
}

struct ModelSelectResponse: Codable {
    let success: Bool
    let message: String
}

struct ModelDownloadRequest: Encodable {
    let modelId: String

    enum CodingKeys: String, CodingKey {
        case modelId = "model_id"
    }
}

// MARK: - Bootstrap

struct BootstrapStepProgress: Codable {
    let percent: Double
    let downloadedMb: Double?
    let totalMb: Double?

    enum CodingKeys: String, CodingKey {
        case percent
        case downloadedMb = "downloaded_mb"
        case totalMb = "total_mb"
    }
}

struct BootstrapStatusResponse: Codable {
    let state: String
    let ready: Bool
    let needsUserInput: Bool
    let progress: [String: BootstrapStepProgress]?
    let error: String?
    let displayMessage: String

    enum CodingKeys: String, CodingKey {
        case state, ready, progress, error
        case needsUserInput = "needs_user_input"
        case displayMessage = "display_message"
    }
}

struct UpgradeRequest: Encodable {
    let projectRoot: String

    enum CodingKeys: String, CodingKey {
        case projectRoot = "project_root"
    }
}

struct UpgradeResponse: Codable {
    let success: Bool
    let restartRequired: Bool
    let message: String

    enum CodingKeys: String, CodingKey {
        case success, message
        case restartRequired = "restart_required"
    }
}

// MARK: - Goals

struct GoalProgressItem: Codable, Identifiable {
    let id: Int
    let goalId: Int
    let note: String
    let source: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, note, source
        case goalId = "goal_id"
        case createdAt = "created_at"
    }

    var sourceBadge: String {
        switch source {
        case "sync": return "Sync"
        case "review": return "Review"
        case "chat": return "Chat"
        case "user": return "Manual"
        default: return source.capitalized
        }
    }

    var formattedDate: String {
        guard let createdAt else { return "" }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: createdAt) {
            let display = DateFormatter()
            display.dateFormat = "MMM d"
            return display.string(from: date)
        }
        return String(createdAt.prefix(10))
    }
}

struct GoalChildItem: Codable, Identifiable {
    let id: Int
    let title: String
    let tier: String
    let status: String
    let priority: String
}

struct SuggestedObjective: Codable {
    let title: String
    let description: String?
    let category: String?
    let tier: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decode(String.self, forKey: .title)
        description = try? c.decodeIfPresent(String.self, forKey: .description)
        category = try? c.decodeIfPresent(String.self, forKey: .category)
        tier = try? c.decodeIfPresent(String.self, forKey: .tier)
    }
}

struct GoalStrategyItem: Codable, Identifiable {
    let id: Int
    let strategyText: String
    let actionItems: [[String: String]]
    let suggestedObjectives: [SuggestedObjective]
    let status: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, status
        case strategyText = "strategy_text"
        case actionItems = "action_items"
        case suggestedObjectives = "suggested_objectives"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        strategyText = try c.decode(String.self, forKey: .strategyText)
        actionItems = (try? c.decodeIfPresent([[String: String]].self, forKey: .actionItems)) ?? []
        suggestedObjectives = (try? c.decodeIfPresent(
            [SuggestedObjective].self, forKey: .suggestedObjectives
        )) ?? []
        status = try c.decode(String.self, forKey: .status)
        createdAt = try? c.decodeIfPresent(String.self, forKey: .createdAt)
    }
}

struct GoalTaskItem: Codable, Identifiable {
    let id: Int
    let title: String
    let priority: String
    let status: String
    let dueDate: String?
    let classification: String?

    enum CodingKeys: String, CodingKey {
        case id, title, priority, status, classification
        case dueDate = "due_date"
    }
}

struct GoalItem: Codable, Identifiable {
    let id: Int
    let title: String
    let tier: String
    let description: String
    let category: String
    let parentId: Int?
    let status: String
    let priority: String
    let targetDate: String?
    let createdAt: String?
    let updatedAt: String?
    let progress: [GoalProgressItem]
    let children: [GoalChildItem]
    let strategies: [GoalStrategyItem]
    let tasks: [GoalTaskItem]

    enum CodingKeys: String, CodingKey {
        case id, title, tier, description, category, status, priority
        case progress, children, strategies, tasks
        case parentId = "parent_id"
        case targetDate = "target_date"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var tierLabel: String {
        switch tier {
        case "long_term": return "Long-term"
        case "mid_term": return "Mid-term"
        case "short_term": return "Short-term"
        default: return tier
        }
    }

    var priorityColor: String {
        switch priority {
        case "high": return "red"
        case "medium": return "orange"
        case "low": return "gray"
        default: return "primary"
        }
    }

    var formattedTargetDate: String? {
        guard let targetDate else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withFullDate]
        if let date = formatter.date(from: String(targetDate.prefix(10))) {
            let display = DateFormatter()
            display.dateFormat = "MMM yyyy"
            return display.string(from: date)
        }
        return String(targetDate.prefix(10))
    }

    /// Empty placeholder shown while loading goal detail.
    static let placeholder = GoalItem(
        id: 0, title: "Loading…", tier: "", description: "",
        category: "", parentId: nil, status: "active", priority: "medium",
        targetDate: nil, createdAt: nil, updatedAt: nil,
        progress: [], children: [], strategies: [], tasks: []
    )
}

struct GoalListResponse: Codable {
    let goals: [GoalItem]
    let count: Int
}

struct GoalRequest: Encodable {
    let title: String
    let tier: String
    var description: String = ""
    var category: String = ""
    var parentId: Int?
    var priority: String = "medium"
    var targetDate: String?

    enum CodingKeys: String, CodingKey {
        case title, tier, description, category, priority
        case parentId = "parent_id"
        case targetDate = "target_date"
    }
}

struct GoalUpdateRequest: Encodable {
    var title: String?
    var description: String?
    var category: String?
    var priority: String?
    var targetDate: String?

    enum CodingKeys: String, CodingKey {
        case title, description, category, priority
        case targetDate = "target_date"
    }
}

struct GoalStatusUpdateRequest: Encodable {
    let status: String
}

struct GoalProgressRequest: Encodable {
    let note: String
    var source: String = "user"
}

struct PlanAcceptRequest: Encodable {
    let planJson: String

    enum CodingKeys: String, CodingKey {
        case planJson = "plan_json"
    }
}

struct GoalChatRequest: Encodable {
    let query: String
}

struct TaskChatRequest: Encodable {
    let query: String
}

struct GoalMessageItem: Codable {
    let role: String
    let content: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case role, content
        case createdAt = "created_at"
    }
}

struct GoalMessagesResponse: Codable {
    let messages: [GoalMessageItem]
    let count: Int
}

struct StrategyAcceptRequest: Encodable {
    // Empty body — the endpoint just needs POST
}

// MARK: - Daily Review

struct ReviewStatusResponse: Codable {
    let due: Bool
    let lastReviewDate: String?

    enum CodingKeys: String, CodingKey {
        case due
        case lastReviewDate = "last_review_date"
    }
}

struct ReviewRespondRequest: Encodable {
    let reviewId: Int
    let response: String

    enum CodingKeys: String, CodingKey {
        case response
        case reviewId = "review_id"
    }
}

struct ReviewSummaryResponse: Codable {
    let summary: String
}

struct ReviewHistoryItem: Codable, Identifiable {
    let id: Int
    let reviewDate: String
    let promptText: String
    let userResponse: String
    let summary: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, summary
        case reviewDate = "review_date"
        case promptText = "prompt_text"
        case userResponse = "user_response"
        case createdAt = "created_at"
    }
}

// MARK: - Agent Queue

struct AgentJobItem: Decodable, Identifiable {
    var id: String { jobId }

    let jobId: String
    let agentId: String
    let query: String
    let priority: Int
    let status: String        // pending | pending_confirmation | running | completed | failed | cancelled
    let source: String         // chat | task | goal | scheduler
    let goalId: Int?
    let taskId: Int?
    let planSummary: String?
    let result: AgentJobResult?
    let error: String?
    let createdAt: Double
    let completedAt: Double?

    enum CodingKeys: String, CodingKey {
        case query, priority, status, source, result, error
        case jobId = "job_id"
        case agentId = "agent_id"
        case goalId = "goal_id"
        case taskId = "task_id"
        case planSummary = "plan_summary"
        case createdAt = "created_at"
        case completedAt = "completed_at"
    }

    var isActive: Bool { status == "pending" || status == "running" }
    var isTerminal: Bool { status == "completed" || status == "failed" || status == "cancelled" }

    var statusLabel: String {
        switch status {
        case "pending": return "Queued"
        case "pending_confirmation": return "Needs Approval"
        case "running": return "Running"
        case "completed": return "Done"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        default: return status.capitalized
        }
    }
}

struct AgentJobResult: Decodable {
    let success: Bool
    let output: String?
    let error: String?

    private enum CodingKeys: String, CodingKey {
        case success, output, error
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        success = (try? c.decode(Bool.self, forKey: .success)) ?? false
        output = try? c.decodeIfPresent(String.self, forKey: .output)
        error = try? c.decodeIfPresent(String.self, forKey: .error)
    }
}

struct AgentConfirmation: Identifiable {
    let id: String          // job_id
    let agentId: String
    let agentName: String
    let message: String
    let params: [String: Any]

    init?(from json: String) {
        guard let data = json.data(using: .utf8),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }

        guard let jobId = dict["job_id"] as? String,
              let agentId = dict["agent_id"] as? String,
              let agentName = dict["agent_name"] as? String,
              let message = dict["message"] as? String
        else { return nil }

        self.id = jobId
        self.agentId = agentId
        self.agentName = agentName
        self.message = message
        self.params = dict["params"] as? [String: Any] ?? [:]
    }
}

struct AgentConfirmRequest: Encodable {
    let jobId: String

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
    }
}

struct AgentQueueResponse: Decodable {
    let jobs: [AgentJobItem]
    let count: Int
    let activeCount: Int

    enum CodingKeys: String, CodingKey {
        case jobs, count
        case activeCount = "active_count"
    }
}

// MARK: - Session State (server-driven lifecycle)

/// Numeric stats from the server's get_stats().
/// The server also returns a `syncs` array of dicts which we ignore in the UI.
struct SessionStats: Codable {
    let emails: Int
    let events: Int
    let pendingTasks: Int
    let activeGoals: Int

    enum CodingKeys: String, CodingKey {
        case emails, events
        case pendingTasks = "pending_tasks"
        case activeGoals = "active_goals"
    }
}

struct SessionStateResponse: Codable {
    /// Server checkpoint — the single authoritative state.
    /// Values: ready, syncing, onboarding, operational, etc.
    let phase: String
    let messages: [SessionMessage]
    let needsResponse: Bool
    let stats: SessionStats?

    enum CodingKeys: String, CodingKey {
        case phase, messages, stats
        case needsResponse = "needs_response"
    }

    /// Custom decoder: tolerate missing/malformed fields so a partial server
    /// response never breaks the UI's ability to read the phase.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        phase = try c.decode(String.self, forKey: .phase)
        messages = (try? c.decodeIfPresent([SessionMessage].self, forKey: .messages)) ?? []
        needsResponse = (try? c.decodeIfPresent(Bool.self, forKey: .needsResponse)) ?? false
        stats = try? c.decodeIfPresent(SessionStats.self, forKey: .stats)
    }
}

struct SessionMessage: Codable {
    let role: String
    let content: String
}

// MARK: - Conversation History

/// A date entry in the chat history sidebar.
struct ConversationDate: Codable, Identifiable, Hashable {
    let date: String           // YYYY-MM-DD
    let preview: String?       // first user message of the day
    let messageCount: Int

    var id: String { date }

    enum CodingKeys: String, CodingKey {
        case date = "day"
        case preview
        case messageCount = "message_count"
    }

    /// Display label: "Today", "Yesterday", or "Feb 22"
    var displayLabel: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let d = formatter.date(from: date) else { return date }

        if Calendar.current.isDateInToday(d) { return "Today" }
        if Calendar.current.isDateInYesterday(d) { return "Yesterday" }

        let display = DateFormatter()
        display.dateFormat = "MMM d"
        return display.string(from: d)
    }
}

struct ConversationDatesResponse: Codable {
    let dates: [ConversationDate]
    let count: Int
}

struct ConversationMessagesResponse: Codable {
    let messages: [ConversationMessageItem]
    let count: Int
}

struct ConversationMessageItem: Codable {
    let role: String
    let content: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case role, content
        case createdAt = "created_at"
    }
}

// MARK: - Configuration

struct LLMConfigResponse: Codable, Equatable {
    let model: String
    let filterModel: String
    let maxTokens: Int
    let temperature: Double
    let topP: Double
    let contextBudgetTokens: Int

    enum CodingKeys: String, CodingKey {
        case model, temperature
        case filterModel = "filter_model"
        case maxTokens = "max_tokens"
        case topP = "top_p"
        case contextBudgetTokens = "context_budget_tokens"
    }
}

struct VoiceConfigResponse: Codable, Equatable {
    let enabled: Bool
    let ttsModel: String
    let ttsVoice: String
    let sttModel: String
    let sampleRate: Int

    enum CodingKeys: String, CodingKey {
        case enabled
        case ttsModel = "tts_model"
        case ttsVoice = "tts_voice"
        case sttModel = "stt_model"
        case sampleRate = "sample_rate"
    }
}

struct PowerConfigResponse: Codable, Equatable {
    let enabled: Bool
    let batteryPauseThreshold: Int
    let batteryDeferHeavyThreshold: Int
    let thermalPauseThreshold: Int
    let thermalDeferHeavyThreshold: Int
    let modelIdleTimeoutMinutes: Int

    enum CodingKeys: String, CodingKey {
        case enabled
        case batteryPauseThreshold = "battery_pause_threshold"
        case batteryDeferHeavyThreshold = "battery_defer_heavy_threshold"
        case thermalPauseThreshold = "thermal_pause_threshold"
        case thermalDeferHeavyThreshold = "thermal_defer_heavy_threshold"
        case modelIdleTimeoutMinutes = "model_idle_timeout_minutes"
    }
}

struct MailConfigResponse: Codable, Equatable {
    let mailboxes: [String]
    let batchSize: Int
    let syncIntervalMinutes: Int
    let initialSyncMonths: Int
    let deepSyncMaxMonths: Int

    enum CodingKeys: String, CodingKey {
        case mailboxes
        case batchSize = "batch_size"
        case syncIntervalMinutes = "sync_interval_minutes"
        case initialSyncMonths = "initial_sync_months"
        case deepSyncMaxMonths = "deep_sync_max_months"
    }
}

struct CalendarConfigResponse: Codable, Equatable {
    let syncWindowPastDays: Int
    let syncWindowFutureDays: Int
    let syncIntervalMinutes: Int

    enum CodingKeys: String, CodingKey {
        case syncWindowPastDays = "sync_window_past_days"
        case syncWindowFutureDays = "sync_window_future_days"
        case syncIntervalMinutes = "sync_interval_minutes"
    }
}

struct AgentsConfigResponse: Codable, Equatable {
    let enabled: Bool
    let routingEnabled: Bool
    let maxExecutionSeconds: Int

    enum CodingKeys: String, CodingKey {
        case enabled
        case routingEnabled = "routing_enabled"
        case maxExecutionSeconds = "max_execution_seconds"
    }
}

struct GoalsConfigResponse: Codable, Equatable {
    let strategyIntervalHours: Int
    let dailyReviewHour: Int
    let maxStrategiesPerRun: Int
    let planHorizonDays: Int

    enum CodingKeys: String, CodingKey {
        case strategyIntervalHours = "strategy_interval_hours"
        case dailyReviewHour = "daily_review_hour"
        case maxStrategiesPerRun = "max_strategies_per_run"
        case planHorizonDays = "plan_horizon_days"
    }
}

struct VlmConfigResponse: Codable, Equatable {
    let enabled: Bool
    let model: String
    let pollIntervalSeconds: Int
    let actionDelayMs: Int

    enum CodingKeys: String, CodingKey {
        case enabled, model
        case pollIntervalSeconds = "poll_interval_seconds"
        case actionDelayMs = "action_delay_ms"
    }
}

struct ConfigResponse: Codable, Equatable {
    let llm: LLMConfigResponse
    let voice: VoiceConfigResponse
    let power: PowerConfigResponse
    let mail: MailConfigResponse
    let calendar: CalendarConfigResponse
    let agents: AgentsConfigResponse
    let goals: GoalsConfigResponse
    let vlm: VlmConfigResponse
}

// MARK: - SSE Event

struct SSEEvent {
    let event: String   // "token", "thinking", "done", "error", "audio_chunk"
    let data: String
}

// MARK: - Chat Message

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    let role: String       // "user" or "assistant" or "system"
    var content: String
    var thinkingContent: String
    var isThinking: Bool
    let timestamp: Date
    var isStreaming: Bool

    init(role: String, content: String, timestamp: Date = Date(), isStreaming: Bool = false) {
        self.role = role
        self.content = content
        self.thinkingContent = ""
        self.isThinking = false
        self.timestamp = timestamp
        self.isStreaming = isStreaming
    }

    static func == (lhs: ChatMessage, rhs: ChatMessage) -> Bool {
        lhs.id == rhs.id
        && lhs.content == rhs.content
        && lhs.thinkingContent == rhs.thinkingContent
        && lhs.isThinking == rhs.isThinking
        && lhs.isStreaming == rhs.isStreaming
    }
}
