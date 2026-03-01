// TaskChatView.swift - Contextual AI chat scoped to a specific task.
//
// Displays the task's details as a header, chat messages, and an input field.
// Messages are persisted task-scoped (not mixed with global or goal chat).
// The coordinator agent helps the user accomplish the task, drafting assets
// for review and reporting where deliverables are stored.
//
// Uses shared ChatMessageList + ChatInputBar components.

import SwiftUI

struct TaskChatView: View {
    let taskId: Int
    @Environment(GivaViewModel.self) private var viewModel

    var body: some View {
        @Bindable var viewModel = viewModel

        VStack(spacing: 0) {
            // Task header
            taskHeader

            Divider()

            // Message list
            ChatMessageList(
                messages: viewModel.taskChatMessages,
                isLoadingModel: viewModel.isLoadingModel,
                isLoading: viewModel.isLoadingTaskChat,
                emptyIcon: "sparkles",
                emptyTitle: "Ask the AI coordinator to help\naccomplish this task.",
                emptySubtitle: "It can draft emails, create documents,\nand break the task into steps."
            )
            .layoutPriority(1)

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
            await viewModel.loadTaskChat(taskId: taskId)
        }
    }

    // MARK: - Task Header

    @ViewBuilder
    private var taskHeader: some View {
        if let task = viewModel.tasks.first(where: { $0.id == taskId }) {
            HStack(alignment: .top, spacing: 10) {
                Circle()
                    .fill(priorityColor(task.priority))
                    .frame(width: 10, height: 10)
                    .padding(.top, 4)

                VStack(alignment: .leading, spacing: 3) {
                    Text(task.title)
                        .font(.system(size: 13, weight: .semibold))
                        .lineLimit(2)

                    HStack(spacing: 6) {
                        Text(task.priority.capitalized)
                            .font(.system(size: 10, weight: .medium))
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(priorityColor(task.priority).opacity(0.15))
                            .foregroundColor(priorityColor(task.priority))
                            .cornerRadius(3)

                        Text(task.status.capitalized)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)

                        if let dueDate = task.formattedDueDate {
                            Label(dueDate, systemImage: "calendar")
                                .font(.system(size: 10))
                                .foregroundColor(.secondary)
                        }
                    }

                    if !task.description.isEmpty {
                        Text(task.description)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                            .lineLimit(2)
                    }
                }

                Spacer()

                Image(systemName: "sparkles")
                    .font(.system(size: 14))
                    .foregroundColor(.purple)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color.purple.opacity(0.04))
        } else {
            HStack {
                ProgressView()
                    .controlSize(.small)
                Text("Loading task...")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(10)
        }
    }

    private func priorityColor(_ priority: String) -> Color {
        switch priority {
        case "high": return .red
        case "medium": return .orange
        case "low": return .gray
        default: return .primary
        }
    }
}
