// TaskChatView.swift - Contextual AI chat scoped to a specific task.
//
// Displays the task's details as a header, chat messages, and an input field.
// Messages are persisted task-scoped (not mixed with global or goal chat).
// The coordinator agent helps the user accomplish the task, drafting assets
// for review and reporting where deliverables are stored.

import SwiftUI

struct TaskChatView: View {
    let taskId: Int
    @Environment(GivaViewModel.self) private var viewModel
    @FocusState private var isInputFocused: Bool

    var body: some View {
        @Bindable var viewModel = viewModel

        VStack(spacing: 0) {
            // Task header
            taskHeader

            Divider()

            // Message list
            ScrollViewReader { proxy in
                ScrollView {
                    if viewModel.isLoadingTaskChat {
                        VStack {
                            ProgressView()
                                .controlSize(.small)
                            Text("Loading conversation...")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .padding(.top, 60)
                    } else if viewModel.taskChatMessages.isEmpty {
                        VStack(spacing: 12) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 32))
                                .foregroundColor(.purple.opacity(0.5))
                            Text("Ask the AI coordinator to help\naccomplish this task.")
                                .font(.callout)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                            Text("It can draft emails, create documents,\nand break the task into steps.")
                                .font(.caption)
                                .foregroundColor(.secondary.opacity(0.7))
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .padding(.top, 40)
                    } else {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(viewModel.taskChatMessages) { message in
                                MessageBubble(
                                    message: message,
                                    isLoadingModel: viewModel.isLoadingModel
                                )
                                .id(message.id)
                            }
                        }
                        .padding(12)
                    }
                }
                .frame(maxHeight: .infinity)
                .onChange(of: viewModel.taskChatMessages.count) { _, _ in
                    if let last = viewModel.taskChatMessages.last {
                        withAnimation(.easeOut(duration: 0.2)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
                .onChange(of: viewModel.taskChatMessages.last?.content.count ?? 0) { _, _ in
                    if let last = viewModel.taskChatMessages.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
                .onChange(of: viewModel.taskChatMessages.last?.thinkingContent.count ?? 0) { _, _ in
                    if let last = viewModel.taskChatMessages.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
            .layoutPriority(1)

            Divider()

            // Input field
            HStack(spacing: 8) {
                GrowingTextInput(
                    text: $viewModel.taskChatInput,
                    placeholder: "Ask the coordinator...",
                    isFocused: $isInputFocused,
                    isDisabled: !viewModel.isChatEnabled || viewModel.isTaskChatStreaming,
                    onSubmit: { viewModel.sendTaskChat(taskId: taskId) }
                )

                if viewModel.isTaskChatStreaming {
                    Button(action: { viewModel.cancelTaskChatStreaming() }) {
                        Image(systemName: "stop.circle.fill")
                            .font(.system(size: 18))
                            .foregroundColor(.red)
                    }
                    .buttonStyle(.plain)
                    .help("Stop generating")
                } else {
                    Button(action: { viewModel.sendTaskChat(taskId: taskId) }) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 18))
                            .foregroundColor(.accentColor)
                    }
                    .buttonStyle(.plain)
                    .disabled(
                        viewModel.taskChatInput
                            .trimmingCharacters(in: .whitespaces).isEmpty
                    )
                    .help("Send message")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
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
