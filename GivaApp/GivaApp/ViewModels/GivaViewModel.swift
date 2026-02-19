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

    // Tasks
    @Published var tasks: [TaskItem] = []
    @Published var isLoadingTasks: Bool = false

    // Status & Profile
    @Published var status: StatusResponse?
    @Published var profile: ProfileResponse?

    // UI
    @Published var currentTab: AppTab = .chat
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    // Active streaming task (for cancellation)
    private var streamTask: Task<Void, Never>?

    /// Connect to the daemon-managed server (called after bootstrap completes).
    func connectToServer() async {
        await serverManager.connectToDaemon()
        if serverManager.isRunning {
            apiService = APIService(baseURL: serverManager.baseURL)
            await refreshStatus()
            await loadProfile()
        }
    }

    // MARK: - Chat

    func sendMessage() {
        let query = currentInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, !isStreaming else { return }

        currentInput = ""
        errorMessage = nil

        // Add user message
        messages.append(ChatMessage(role: "user", content: query))

        // Add placeholder assistant message
        messages.append(ChatMessage(role: "assistant", content: "", isStreaming: true))
        isStreaming = true

        streamTask = Task {
            guard let api = apiService else {
                appendSystemMessage("Server is not running. Please wait for startup.")
                isStreaming = false
                return
            }
            do {
                let stream = api.streamChat(query: query)
                for try await event in stream {
                    if event.event == "token" {
                        appendToLastMessage(event.data)
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
        finalizeLastMessage()
        isStreaming = false
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
                    if event.event == "token" {
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

    private func finalizeLastMessage() {
        guard !messages.isEmpty else { return }
        messages[messages.count - 1].isStreaming = false
    }

    private func appendSystemMessage(_ text: String) {
        messages.append(ChatMessage(role: "assistant", content: text))
    }
}
