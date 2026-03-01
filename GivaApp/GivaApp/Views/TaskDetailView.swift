// TaskDetailView.swift - Task detail view: title, description, source links, chat.
//
// Displayed as the content pane when a task is selected in the sidebar.
// Vertical stack: header (title + metadata), description, source deep link,
// then chat history (including agent action logs) with input field.

import SwiftUI

struct TaskDetailView: View {
    let taskId: Int
    @Environment(GivaViewModel.self) private var viewModel

    var body: some View {
        @Bindable var viewModel = viewModel

        VStack(spacing: 0) {
            // Header: title + metadata
            taskHeader

            Divider()

            // Source link (above chat messages)
            if let detail = viewModel.taskDetail, detail.id == taskId {
                sourceSection(detail)
            }

            // Chat messages (including agent action logs)
            ChatMessageList(
                messages: viewModel.taskChatMessages,
                isLoadingModel: viewModel.isLoadingModel,
                isLoading: viewModel.isLoadingTaskChat,
                emptyIcon: "sparkles",
                emptyTitle: "Ask the AI coordinator to help\naccomplish this task.",
                emptySubtitle: "It can draft emails, create documents,\nand break the task into steps."
            )
            .layoutPriority(1)

            // Error banner
            if let error = viewModel.taskChatError {
                taskErrorBanner(error)
            }

            Divider()

            // Input field
            ChatInputBar(
                text: $viewModel.taskChatInput,
                placeholder: "Ask the coordinator...",
                isDisabled: !viewModel.isChatEnabled || viewModel.isTaskChatStreaming,
                isStreaming: viewModel.isTaskChatStreaming,
                onSubmit: { viewModel.sendTaskChat(taskId: taskId) },
                onStop: { viewModel.cancelTaskChatStreaming() }
            )
        }
        .task(id: taskId) {
            await viewModel.loadTaskDetail(taskId: taskId)
            await viewModel.loadTaskChat(taskId: taskId, forceReload: true)
        }
    }

    // MARK: - Task Header

    @ViewBuilder
    private var taskHeader: some View {
        if let detail = viewModel.taskDetail, detail.id == taskId {
            VStack(alignment: .leading, spacing: 6) {
                // Title
                Text(detail.title)
                    .font(.system(size: 15, weight: .semibold))
                    .lineLimit(3)

                // Metadata row
                HStack(spacing: 8) {
                    // Priority badge
                    Text(detail.priority.capitalized)
                        .font(.system(size: 10, weight: .medium))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(priorityColor(detail.priority).opacity(0.15))
                        .foregroundColor(priorityColor(detail.priority))
                        .cornerRadius(4)

                    // Status
                    Text(detail.status.capitalized)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)

                    // Due date
                    if let dueDate = detail.formattedDueDate {
                        Label(dueDate, systemImage: "calendar")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }

                    // Classification
                    if let classification = detail.classification {
                        Text(classification.replacingOccurrences(of: "_", with: " ").capitalized)
                            .font(.system(size: 9, weight: .medium))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.secondary.opacity(0.1))
                            .foregroundColor(.secondary)
                            .cornerRadius(3)
                    }

                    Spacer()

                    // Created date
                    if let created = detail.formattedCreatedDate {
                        Text("Created \(created)")
                            .font(.system(size: 9))
                            .foregroundColor(.secondary.opacity(0.7))
                    }
                }

                // Description
                if !detail.description.isEmpty {
                    Text(detail.description)
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                        .lineLimit(5)
                        .padding(.top, 2)
                }

                // Goal link
                if let goalTitle = detail.goalTitle {
                    HStack(spacing: 4) {
                        Image(systemName: "flag")
                            .font(.system(size: 10))
                            .foregroundColor(.purple)
                        Text("Goal: \(goalTitle)")
                            .font(.system(size: 10))
                            .foregroundColor(.purple)
                    }
                    .padding(.top, 2)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        } else if viewModel.isLoadingTaskDetail {
            HStack {
                ProgressView()
                    .controlSize(.small)
                Text("Loading task...")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(12)
        } else if let task = viewModel.tasks.first(where: { $0.id == taskId }) {
            // Fallback to basic task info from list
            VStack(alignment: .leading, spacing: 4) {
                Text(task.title)
                    .font(.system(size: 15, weight: .semibold))
                    .lineLimit(3)
                HStack(spacing: 6) {
                    Text(task.priority.capitalized)
                        .font(.system(size: 10, weight: .medium))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(priorityColor(task.priority).opacity(0.15))
                        .foregroundColor(priorityColor(task.priority))
                        .cornerRadius(4)
                    Text(task.status.capitalized)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
    }

    // MARK: - Source Section

    @ViewBuilder
    private func sourceSection(_ detail: TaskDetailResponse) -> some View {
        if let source = detail.source {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Image(systemName: sourceIcon(source.sourceType))
                        .font(.system(size: 12))
                        .foregroundColor(.accentColor)

                    VStack(alignment: .leading, spacing: 1) {
                        Text(source.title)
                            .font(.system(size: 11, weight: .medium))
                            .lineLimit(1)

                        if !source.subtitle.isEmpty {
                            Text(source.subtitle)
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }

                    Spacer()

                    if let date = source.date {
                        Text(formatSourceDate(date))
                            .font(.system(size: 9))
                            .foregroundColor(.secondary.opacity(0.7))
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(Color.accentColor.opacity(0.04))
        }
    }

    private func sourceIcon(_ type: String) -> String {
        switch type {
        case "email": return "envelope"
        case "event": return "calendar"
        case "chat": return "bubble.left"
        default: return "doc"
        }
    }

    private func formatSourceDate(_ iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: iso) {
            let display = DateFormatter()
            display.dateFormat = "MMM d"
            return display.string(from: date)
        }
        // Try date-only
        formatter.formatOptions = [.withFullDate]
        if let date = formatter.date(from: String(iso.prefix(10))) {
            let display = DateFormatter()
            display.dateFormat = "MMM d"
            return display.string(from: date)
        }
        return String(iso.prefix(10))
    }

    // MARK: - Error Banner

    private func taskErrorBanner(_ message: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 10))
                .foregroundColor(.yellow)
            Text(message)
                .font(.system(size: 10))
                .foregroundColor(.primary)
                .lineLimit(2)
            Spacer()
            Button(action: { viewModel.taskChatError = nil }) {
                Image(systemName: "xmark")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(Color.yellow.opacity(0.1))
    }

    // MARK: - Helpers

    private func priorityColor(_ priority: String) -> Color {
        switch priority {
        case "high": return .red
        case "medium": return .orange
        case "low": return .gray
        default: return .primary
        }
    }
}
