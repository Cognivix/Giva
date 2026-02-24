// APIModelsTests.swift - Tests for Codable model round-trips and computed properties.

import Foundation
import Testing
@testable import GivaApp

@Suite("APIModels Codable")
struct APIModelsCodableTests {

    // MARK: - HealthResponse

    @Test("HealthResponse decodes from JSON")
    func healthResponseDecode() throws {
        let json = """
        {"status": "ok", "version": "0.1.0", "commit": "abc123"}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(HealthResponse.self, from: json)
        #expect(resp.status == "ok")
        #expect(resp.version == "0.1.0")
        #expect(resp.commit == "abc123")
    }

    // MARK: - SyncInfoItem

    @Test("SyncInfoItem decodes snake_case keys")
    func syncInfoDecode() throws {
        let json = """
        {"source": "mail:INBOX", "last_sync": "2026-01-01T10:00:00", "last_count": 42, "last_status": "success"}
        """.data(using: .utf8)!
        let item = try JSONDecoder().decode(SyncInfoItem.self, from: json)
        #expect(item.source == "mail:INBOX")
        #expect(item.lastSync == "2026-01-01T10:00:00")
        #expect(item.lastCount == 42)
        #expect(item.lastStatus == "success")
    }

    @Test("SyncInfoItem handles null last_sync")
    func syncInfoNullSync() throws {
        let json = """
        {"source": "calendar", "last_sync": null, "last_count": 0, "last_status": "never"}
        """.data(using: .utf8)!
        let item = try JSONDecoder().decode(SyncInfoItem.self, from: json)
        #expect(item.lastSync == nil)
    }

    // MARK: - StatusResponse

    @Test("StatusResponse decodes with snake_case keys")
    func statusResponseDecode() throws {
        let json = """
        {"emails": 150, "events": 30, "pending_tasks": 5, "syncs": [], "model": "qwen3-30b", "model_loaded": true}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(StatusResponse.self, from: json)
        #expect(resp.emails == 150)
        #expect(resp.pendingTasks == 5)
        #expect(resp.modelLoaded == true)
    }

    // MARK: - TaskItem

    @Test("TaskItem decodes with computed properties")
    func taskItemDecode() throws {
        let json = """
        {"id": 1, "title": "Review PR", "description": "Check the latest PR",
         "source_type": "email", "source_id": 42, "priority": "high",
         "due_date": "2026-03-15", "status": "pending", "created_at": "2026-01-01T00:00:00"}
        """.data(using: .utf8)!
        let task = try JSONDecoder().decode(TaskItem.self, from: json)
        #expect(task.id == 1)
        #expect(task.title == "Review PR")
        #expect(task.sourceType == "email")
        #expect(task.sourceId == 42)
        #expect(task.dueDate == "2026-03-15")
    }

    @Test("TaskItem priorityColor maps correctly")
    func taskItemPriorityColor() throws {
        let makeTask = { (priority: String) -> TaskItem in
            let json = """
            {"id": 1, "title": "T", "description": "", "source_type": "e",
             "source_id": 0, "priority": "\(priority)", "due_date": null,
             "status": "pending", "created_at": null}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(TaskItem.self, from: json)
        }

        #expect(makeTask("high").priorityColor == "red")
        #expect(makeTask("medium").priorityColor == "orange")
        #expect(makeTask("low").priorityColor == "gray")
        #expect(makeTask("unknown").priorityColor == "primary")
    }

    @Test("TaskItem formattedDueDate formats ISO date")
    func taskItemFormattedDueDate() throws {
        let json = """
        {"id": 1, "title": "T", "description": "", "source_type": "e",
         "source_id": 0, "priority": "medium", "due_date": "2026-03-15",
         "status": "pending", "created_at": null}
        """.data(using: .utf8)!
        let task = try JSONDecoder().decode(TaskItem.self, from: json)
        let formatted = task.formattedDueDate
        #expect(formatted != nil)
        #expect(formatted!.contains("Mar"))
        #expect(formatted!.contains("15"))
    }

    @Test("TaskItem formattedDueDate returns nil when no date")
    func taskItemNoDueDate() throws {
        let json = """
        {"id": 1, "title": "T", "description": "", "source_type": "e",
         "source_id": 0, "priority": "medium", "due_date": null,
         "status": "pending", "created_at": null}
        """.data(using: .utf8)!
        let task = try JSONDecoder().decode(TaskItem.self, from: json)
        #expect(task.formattedDueDate == nil)
    }

    // MARK: - UpdateTaskStatusResponse

    @Test("UpdateTaskStatusResponse decodes snake_case")
    func updateTaskStatusDecode() throws {
        let json = """
        {"success": true, "task_id": 42, "status": "done"}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(UpdateTaskStatusResponse.self, from: json)
        #expect(resp.success == true)
        #expect(resp.taskId == 42)
        #expect(resp.status == "done")
    }

    // MARK: - ProfileResponse

    @Test("ProfileResponse decodes snake_case keys")
    func profileResponseDecode() throws {
        let json = """
        {"display_name": "Alice", "email_address": "alice@test.com",
         "top_topics": ["budgets", "meetings"], "avg_response_time_min": 25.5,
         "email_volume_daily": 12.0, "summary": "Active user",
         "updated_at": "2026-01-01"}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(ProfileResponse.self, from: json)
        #expect(resp.displayName == "Alice")
        #expect(resp.emailAddress == "alice@test.com")
        #expect(resp.topTopics.count == 2)
        #expect(resp.avgResponseTimeMin == 25.5)
    }

    // MARK: - SyncResponse

    @Test("SyncResponse decodes snake_case keys")
    func syncResponseDecode() throws {
        let json = """
        {"mail_synced": 10, "mail_filtered": 3, "events_synced": 5,
         "profile_updated": true, "needs_onboarding": false}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(SyncResponse.self, from: json)
        #expect(resp.mailSynced == 10)
        #expect(resp.mailFiltered == 3)
        #expect(resp.eventsSynced == 5)
        #expect(resp.profileUpdated == true)
        #expect(resp.needsOnboarding == false)
    }

    // MARK: - OnboardingStatusResponse

    @Test("OnboardingStatusResponse decodes correctly")
    func onboardingStatusDecode() throws {
        let json = """
        {"needs_onboarding": true, "onboarding_step": 2, "onboarding_completed": false}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(OnboardingStatusResponse.self, from: json)
        #expect(resp.needsOnboarding == true)
        #expect(resp.onboardingStep == 2)
        #expect(resp.onboardingCompleted == false)
    }

    // MARK: - ExtractResponse

    @Test("ExtractResponse decodes snake_case")
    func extractResponseDecode() throws {
        let json = """
        {"tasks_extracted": 7}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(ExtractResponse.self, from: json)
        #expect(resp.tasksExtracted == 7)
    }

    // MARK: - HardwareInfo

    @Test("HardwareInfo decodes snake_case")
    func hardwareInfoDecode() throws {
        let json = """
        {"chip": "Apple M4 Max", "ram_gb": 128, "gpu_cores": 40}
        """.data(using: .utf8)!
        let info = try JSONDecoder().decode(HardwareInfo.self, from: json)
        #expect(info.chip == "Apple M4 Max")
        #expect(info.ramGb == 128)
        #expect(info.gpuCores == 40)
    }

    // MARK: - ModelInfo

    @Test("ModelInfo decodes with computed properties")
    func modelInfoDecode() throws {
        let json = """
        {"model_id": "mlx-community/Qwen3-30B-A3B-4bit", "size_gb": 17.5,
         "params": "30B", "quant": "4bit", "downloads": 1500000,
         "is_downloaded": true, "download_status": "complete"}
        """.data(using: .utf8)!
        let info = try JSONDecoder().decode(ModelInfo.self, from: json)
        #expect(info.modelId == "mlx-community/Qwen3-30B-A3B-4bit")
        #expect(info.displayName == "Qwen3-30B-A3B-4bit")
        #expect(info.isDownloaded == true)
        #expect(info.isPartiallyDownloaded == false)
    }

    @Test("ModelInfo sizeString formats GB and MB")
    func modelInfoSizeString() throws {
        let makeInfo = { (sizeGb: Double) -> ModelInfo in
            let json = """
            {"model_id": "test", "size_gb": \(sizeGb), "params": "8B",
             "quant": "4bit", "downloads": 100, "is_downloaded": false,
             "download_status": "not_downloaded"}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(ModelInfo.self, from: json)
        }

        #expect(makeInfo(17.5).sizeString == "17.5 GB")
        #expect(makeInfo(1.0).sizeString == "1.0 GB")
        #expect(makeInfo(0.5).sizeString == "512 MB")
    }

    @Test("ModelInfo downloadsString formats K and M")
    func modelInfoDownloadsString() throws {
        let makeInfo = { (downloads: Int) -> ModelInfo in
            let json = """
            {"model_id": "test", "size_gb": 1.0, "params": "8B",
             "quant": "4bit", "downloads": \(downloads), "is_downloaded": false,
             "download_status": "not_downloaded"}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(ModelInfo.self, from: json)
        }

        #expect(makeInfo(500).downloadsString == "500")
        #expect(makeInfo(1500).downloadsString.contains("K"))
        #expect(makeInfo(1500000).downloadsString.contains("M"))
    }

    @Test("ModelInfo isPartiallyDownloaded detects partial status")
    func modelInfoPartial() throws {
        let json = """
        {"model_id": "test", "size_gb": 1.0, "params": "8B", "quant": "4bit",
         "downloads": 100, "is_downloaded": false, "download_status": "partial"}
        """.data(using: .utf8)!
        let info = try JSONDecoder().decode(ModelInfo.self, from: json)
        #expect(info.isPartiallyDownloaded == true)
    }

    // MARK: - ModelStatusResponse

    @Test("ModelStatusResponse decodes snake_case")
    func modelStatusDecode() throws {
        let json = """
        {"setup_completed": true, "current_assistant": "model-a",
         "current_filter": "model-b",
         "hardware": {"chip": "M4", "ram_gb": 64, "gpu_cores": 30}}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(ModelStatusResponse.self, from: json)
        #expect(resp.setupCompleted == true)
        #expect(resp.currentAssistant == "model-a")
    }

    // MARK: - BootstrapStatusResponse

    @Test("BootstrapStatusResponse decodes snake_case")
    func bootstrapStatusDecode() throws {
        let json = """
        {"state": "ready", "ready": true, "needs_user_input": false,
         "progress": null, "error": null, "display_message": "Ready"}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(BootstrapStatusResponse.self, from: json)
        #expect(resp.state == "ready")
        #expect(resp.ready == true)
        #expect(resp.needsUserInput == false)
        #expect(resp.displayMessage == "Ready")
    }

    // MARK: - UpgradeResponse

    @Test("UpgradeResponse decodes snake_case")
    func upgradeResponseDecode() throws {
        let json = """
        {"success": true, "restart_required": true, "message": "Upgraded"}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(UpgradeResponse.self, from: json)
        #expect(resp.success == true)
        #expect(resp.restartRequired == true)
    }
}

// MARK: - Goal Models

@Suite("Goal Models Codable")
struct GoalModelsCodableTests {

    @Test("GoalProgressItem decodes with computed properties")
    func goalProgressDecode() throws {
        let json = """
        {"id": 1, "goal_id": 10, "note": "Completed milestone",
         "source": "user", "created_at": "2026-02-15T10:30:00.000000"}
        """.data(using: .utf8)!
        let item = try JSONDecoder().decode(GoalProgressItem.self, from: json)
        #expect(item.id == 1)
        #expect(item.goalId == 10)
        #expect(item.note == "Completed milestone")
        #expect(item.sourceBadge == "Manual")
    }

    @Test("GoalProgressItem sourceBadge maps correctly")
    func goalProgressSourceBadge() throws {
        let makeProgress = { (source: String) -> GoalProgressItem in
            let json = """
            {"id": 1, "goal_id": 1, "note": "n", "source": "\(source)", "created_at": null}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(GoalProgressItem.self, from: json)
        }

        #expect(makeProgress("sync").sourceBadge == "Sync")
        #expect(makeProgress("review").sourceBadge == "Review")
        #expect(makeProgress("chat").sourceBadge == "Chat")
        #expect(makeProgress("user").sourceBadge == "Manual")
        #expect(makeProgress("other").sourceBadge == "Other")
    }

    @Test("GoalItem decodes with all nested types")
    func goalItemDecode() throws {
        let json = """
        {"id": 1, "title": "Learn Rust", "tier": "long_term",
         "description": "Master Rust programming", "category": "skills",
         "parent_id": null, "status": "active", "priority": "high",
         "target_date": "2026-12-31", "created_at": "2026-01-01T00:00:00",
         "updated_at": "2026-02-01T00:00:00",
         "progress": [], "children": [], "strategies": [], "tasks": []}
        """.data(using: .utf8)!
        let goal = try JSONDecoder().decode(GoalItem.self, from: json)
        #expect(goal.id == 1)
        #expect(goal.title == "Learn Rust")
        #expect(goal.tierLabel == "Long-term")
        #expect(goal.priorityColor == "red")
    }

    @Test("GoalItem tierLabel maps correctly")
    func goalItemTierLabel() throws {
        let makeGoal = { (tier: String) -> GoalItem in
            let json = """
            {"id": 1, "title": "G", "tier": "\(tier)", "description": "",
             "category": "", "parent_id": null, "status": "active",
             "priority": "medium", "target_date": null, "created_at": null,
             "updated_at": null, "progress": [], "children": [],
             "strategies": [], "tasks": []}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(GoalItem.self, from: json)
        }

        #expect(makeGoal("long_term").tierLabel == "Long-term")
        #expect(makeGoal("mid_term").tierLabel == "Mid-term")
        #expect(makeGoal("short_term").tierLabel == "Short-term")
        #expect(makeGoal("custom").tierLabel == "custom")
    }

    @Test("GoalItem placeholder is valid")
    func goalItemPlaceholder() {
        let placeholder = GoalItem.placeholder
        #expect(placeholder.id == 0)
        #expect(placeholder.title == "Loading…")
    }

    @Test("GoalChildItem decodes correctly")
    func goalChildDecode() throws {
        let json = """
        {"id": 5, "title": "Sub-goal", "tier": "mid_term",
         "status": "active", "priority": "high"}
        """.data(using: .utf8)!
        let child = try JSONDecoder().decode(GoalChildItem.self, from: json)
        #expect(child.id == 5)
        #expect(child.title == "Sub-goal")
    }

    @Test("GoalTaskItem decodes with snake_case due_date")
    func goalTaskDecode() throws {
        let json = """
        {"id": 3, "title": "Write tests", "priority": "high",
         "status": "pending", "due_date": "2026-06-01"}
        """.data(using: .utf8)!
        let task = try JSONDecoder().decode(GoalTaskItem.self, from: json)
        #expect(task.dueDate == "2026-06-01")
    }

    @Test("GoalStrategyItem decodes with fallback for missing arrays")
    func goalStrategyDecode() throws {
        let json = """
        {"id": 1, "strategy_text": "Focus on fundamentals",
         "action_items": [{"step": "practice"}],
         "suggested_objectives": [{"title": "Objective 1"}],
         "status": "proposed", "created_at": "2026-01-01T00:00:00"}
        """.data(using: .utf8)!
        let strategy = try JSONDecoder().decode(GoalStrategyItem.self, from: json)
        #expect(strategy.strategyText == "Focus on fundamentals")
        #expect(strategy.actionItems.count == 1)
        #expect(strategy.suggestedObjectives.count == 1)
    }

    @Test("GoalStrategyItem handles missing optional arrays gracefully")
    func goalStrategyMissingArrays() throws {
        let json = """
        {"id": 1, "strategy_text": "Plan", "status": "proposed"}
        """.data(using: .utf8)!
        let strategy = try JSONDecoder().decode(GoalStrategyItem.self, from: json)
        #expect(strategy.actionItems.isEmpty)
        #expect(strategy.suggestedObjectives.isEmpty)
        #expect(strategy.createdAt == nil)
    }
}

// MARK: - Agent Queue Models

@Suite("Agent Queue Models")
struct AgentQueueModelsCodableTests {

    @Test("AgentJobItem decodes with computed properties")
    func agentJobDecode() throws {
        let json = """
        {"job_id": "abc-123", "agent_id": "email_drafter",
         "query": "Draft reply", "priority": 10, "status": "pending",
         "source": "chat", "goal_id": null, "task_id": null,
         "plan_summary": null, "result": null, "error": null,
         "created_at": 1700000000.0, "completed_at": null}
        """.data(using: .utf8)!
        let job = try JSONDecoder().decode(AgentJobItem.self, from: json)
        #expect(job.jobId == "abc-123")
        #expect(job.isActive == true)
        #expect(job.isTerminal == false)
        #expect(job.statusLabel == "Queued")
    }

    @Test("AgentJobItem status labels map correctly")
    func agentJobStatusLabels() throws {
        let makeJob = { (status: String) -> AgentJobItem in
            let json = """
            {"job_id": "j", "agent_id": "a", "query": "q", "priority": 1,
             "status": "\(status)", "source": "chat", "goal_id": null,
             "task_id": null, "plan_summary": null, "result": null,
             "error": null, "created_at": 0.0, "completed_at": null}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(AgentJobItem.self, from: json)
        }

        #expect(makeJob("pending").statusLabel == "Queued")
        #expect(makeJob("pending_confirmation").statusLabel == "Needs Approval")
        #expect(makeJob("running").statusLabel == "Running")
        #expect(makeJob("completed").statusLabel == "Done")
        #expect(makeJob("failed").statusLabel == "Failed")
        #expect(makeJob("cancelled").statusLabel == "Cancelled")
    }

    @Test("AgentJobItem isActive and isTerminal")
    func agentJobActiveTerminal() throws {
        let makeJob = { (status: String) -> AgentJobItem in
            let json = """
            {"job_id": "j", "agent_id": "a", "query": "q", "priority": 1,
             "status": "\(status)", "source": "chat", "goal_id": null,
             "task_id": null, "plan_summary": null, "result": null,
             "error": null, "created_at": 0.0, "completed_at": null}
            """.data(using: .utf8)!
            return try! JSONDecoder().decode(AgentJobItem.self, from: json)
        }

        #expect(makeJob("pending").isActive == true)
        #expect(makeJob("running").isActive == true)
        #expect(makeJob("completed").isActive == false)
        #expect(makeJob("completed").isTerminal == true)
        #expect(makeJob("failed").isTerminal == true)
        #expect(makeJob("cancelled").isTerminal == true)
        #expect(makeJob("pending").isTerminal == false)
    }

    @Test("AgentJobResult decodes with fallback for missing success")
    func agentJobResultDecode() throws {
        let json = """
        {"output": "Done", "error": null}
        """.data(using: .utf8)!
        let result = try JSONDecoder().decode(AgentJobResult.self, from: json)
        #expect(result.success == false)  // Defaults to false when missing
        #expect(result.output == "Done")
    }

    @Test("AgentConfirmation parses valid JSON")
    func agentConfirmationParse() {
        let json = """
        {"job_id": "j1", "agent_id": "email_drafter", "agent_name": "Email Drafter",
         "message": "Send this email?", "params": {"to": "bob@test.com"}}
        """
        let conf = AgentConfirmation(from: json)
        #expect(conf != nil)
        #expect(conf!.id == "j1")
        #expect(conf!.agentName == "Email Drafter")
        #expect(conf!.message == "Send this email?")
    }

    @Test("AgentConfirmation returns nil for invalid JSON")
    func agentConfirmationInvalid() {
        let conf = AgentConfirmation(from: "not json")
        #expect(conf == nil)
    }

    @Test("AgentConfirmation returns nil for missing required fields")
    func agentConfirmationMissingFields() {
        let json = """
        {"job_id": "j1"}
        """
        let conf = AgentConfirmation(from: json)
        #expect(conf == nil)
    }

    @Test("AgentQueueResponse decodes snake_case")
    func agentQueueDecode() throws {
        let json = """
        {"jobs": [], "count": 0, "active_count": 0}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(AgentQueueResponse.self, from: json)
        #expect(resp.count == 0)
        #expect(resp.activeCount == 0)
    }
}

// MARK: - Session & Conversation Models

@Suite("Session Models")
struct SessionModelsCodableTests {

    @Test("SessionStateResponse decodes with tolerance for missing fields")
    func sessionStateDecode() throws {
        let json = """
        {"phase": "operational"}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(SessionStateResponse.self, from: json)
        #expect(resp.phase == "operational")
        #expect(resp.messages.isEmpty)
        #expect(resp.needsResponse == false)
        #expect(resp.stats == nil)
    }

    @Test("SessionStateResponse decodes full payload")
    func sessionStateFullDecode() throws {
        let json = """
        {"phase": "onboarding",
         "messages": [{"role": "assistant", "content": "Welcome!"}],
         "needs_response": true,
         "stats": {"emails": 100, "events": 20, "pending_tasks": 5, "active_goals": 3}}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(SessionStateResponse.self, from: json)
        #expect(resp.phase == "onboarding")
        #expect(resp.messages.count == 1)
        #expect(resp.needsResponse == true)
        #expect(resp.stats?.emails == 100)
        #expect(resp.stats?.activeGoals == 3)
    }

    @Test("ReviewStatusResponse decodes snake_case")
    func reviewStatusDecode() throws {
        let json = """
        {"due": true, "last_review_date": "2026-02-20"}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(ReviewStatusResponse.self, from: json)
        #expect(resp.due == true)
        #expect(resp.lastReviewDate == "2026-02-20")
    }

    @Test("ReviewHistoryItem decodes all fields")
    func reviewHistoryDecode() throws {
        let json = """
        {"id": 1, "review_date": "2026-02-20", "prompt_text": "How was your day?",
         "user_response": "Great", "summary": "Productive day",
         "created_at": "2026-02-20T18:00:00"}
        """.data(using: .utf8)!
        let item = try JSONDecoder().decode(ReviewHistoryItem.self, from: json)
        #expect(item.reviewDate == "2026-02-20")
        #expect(item.promptText == "How was your day?")
        #expect(item.userResponse == "Great")
    }

    @Test("GoalMessagesResponse decodes correctly")
    func goalMessagesDecode() throws {
        let json = """
        {"messages": [
            {"role": "user", "content": "How's my goal?", "created_at": "2026-01-01"},
            {"role": "assistant", "content": "Great progress!", "created_at": "2026-01-01"}
         ], "count": 2}
        """.data(using: .utf8)!
        let resp = try JSONDecoder().decode(GoalMessagesResponse.self, from: json)
        #expect(resp.count == 2)
        #expect(resp.messages[0].role == "user")
    }

    @Test("ConversationDate displayLabel shows Today/Yesterday")
    func conversationDateLabel() throws {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        let todayStr = formatter.string(from: Date())

        let json = """
        {"day": "\(todayStr)", "preview": "Hello", "message_count": 5}
        """.data(using: .utf8)!
        let dateItem = try JSONDecoder().decode(ConversationDate.self, from: json)
        #expect(dateItem.displayLabel == "Today")
        #expect(dateItem.messageCount == 5)
    }
}

// MARK: - ChatMessage

@Suite("ChatMessage")
struct ChatMessageTests {

    @Test("ChatMessage initializer sets defaults")
    func chatMessageDefaults() {
        let msg = ChatMessage(role: "user", content: "Hello")
        #expect(msg.role == "user")
        #expect(msg.content == "Hello")
        #expect(msg.thinkingContent == "")
        #expect(msg.isThinking == false)
        #expect(msg.isStreaming == false)
    }

    @Test("ChatMessage equality compares relevant fields")
    func chatMessageEquality() {
        var msg1 = ChatMessage(role: "assistant", content: "Hi")
        var msg2 = msg1
        #expect(msg1 == msg2)

        msg2.content = "Different"
        #expect(msg1 != msg2)
    }

    @Test("ChatMessage streaming flag")
    func chatMessageStreaming() {
        let msg = ChatMessage(role: "assistant", content: "", isStreaming: true)
        #expect(msg.isStreaming == true)
    }
}

// MARK: - SSEEvent

@Suite("SSEEvent")
struct SSEEventTests {

    @Test("SSEEvent stores event type and data")
    func sseEventInit() {
        let event = SSEEvent(event: "token", data: "Hello")
        #expect(event.event == "token")
        #expect(event.data == "Hello")
    }
}
