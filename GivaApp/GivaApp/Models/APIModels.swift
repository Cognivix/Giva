// APIModels.swift - Codable structs mirroring the Python API response schemas.

import Foundation

// MARK: - Request Models

struct ChatRequest: Encodable {
    let query: String
}

struct UpdateTaskStatusRequest: Encodable {
    let status: String
}

// MARK: - Response Models

struct HealthResponse: Codable {
    let status: String
    let version: String
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
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, description, priority, status
        case sourceType = "source_type"
        case sourceId = "source_id"
        case dueDate = "due_date"
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

    enum CodingKeys: String, CodingKey {
        case mailSynced = "mail_synced"
        case mailFiltered = "mail_filtered"
        case eventsSynced = "events_synced"
        case profileUpdated = "profile_updated"
    }
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

// MARK: - SSE Event

struct SSEEvent {
    let event: String   // "token", "done", "error"
    let data: String
}

// MARK: - Chat Message

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: String       // "user" or "assistant" or "system"
    var content: String
    let timestamp: Date
    var isStreaming: Bool

    init(role: String, content: String, timestamp: Date = Date(), isStreaming: Bool = false) {
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.isStreaming = isStreaming
    }
}
