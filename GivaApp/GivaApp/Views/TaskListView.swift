// TaskListView.swift - List of pending tasks with priority indicators and actions.

import SwiftUI

struct TaskListView: View {
    @Environment(GivaViewModel.self) private var viewModel

    var body: some View {
        Group {
            if viewModel.isLoadingTasks {
                VStack {
                    ProgressView()
                        .controlSize(.small)
                    Text("Loading tasks...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if viewModel.tasks.isEmpty {
                VStack(spacing: 12) {
                    Image(systemName: "checkmark.circle")
                        .font(.system(size: 32))
                        .foregroundColor(.secondary.opacity(0.5))
                    Text("No pending tasks")
                        .font(.callout)
                        .foregroundColor(.secondary)
                    Text("Tasks will appear as Giva identifies\nthem from your emails and calendar.")
                        .font(.caption)
                        .foregroundColor(.secondary.opacity(0.7))
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(.top, 40)
            } else {
                ScrollView {
                    LazyVStack(spacing: 4) {
                        ForEach(viewModel.tasks) { task in
                            TaskRow(task: task, onStatusChange: { status in
                                Task {
                                    await viewModel.updateTaskStatus(taskId: task.id, status: status)
                                }
                            }, onAIRequest: {
                                Task { await viewModel.requestTaskAI(taskId: task.id) }
                            })
                        }
                    }
                    .padding(8)
                }
            }
        }
        .onAppear {
            Task { await viewModel.loadTasks() }
        }
    }
}

struct TaskRow: View {
    let task: TaskItem
    let onStatusChange: (String) -> Void
    var onAIRequest: (() -> Void)? = nil

    @State private var isHovering = false

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            // Priority indicator
            Circle()
                .fill(priorityColor)
                .frame(width: 8, height: 8)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: 2) {
                Text(task.title)
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(2)

                HStack(spacing: 6) {
                    if let dueDate = task.formattedDueDate {
                        Label(dueDate, systemImage: "calendar")
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }

                    Text(task.priority.capitalized)
                        .font(.system(size: 9, weight: .medium))
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(priorityColor.opacity(0.15))
                        .foregroundColor(priorityColor)
                        .cornerRadius(3)

                    Text(task.sourceType.capitalized)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            // Action buttons (visible on hover)
            if isHovering {
                HStack(spacing: 4) {
                    if let onAI = onAIRequest {
                        Button(action: onAI) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 14))
                                .foregroundColor(.purple)
                        }
                        .buttonStyle(.plain)
                        .help("Plan with AI")
                    }

                    Button(action: { onStatusChange("done") }) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 16))
                            .foregroundColor(.green)
                    }
                    .buttonStyle(.plain)
                    .help("Mark as done")

                    Button(action: { onStatusChange("dismissed") }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 16))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .help("Dismiss")
                }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(isHovering ? Color.primary.opacity(0.04) : Color.clear)
        .cornerRadius(6)
        .onHover { hovering in
            isHovering = hovering
        }
        .contextMenu {
            Button {
                if let onAI = onAIRequest { onAI() }
            } label: {
                Label("Plan with AI", systemImage: "sparkles")
            }

            Divider()

            Button {
                onStatusChange("done")
            } label: {
                Label("Mark as Done", systemImage: "checkmark.circle")
            }

            Button {
                onStatusChange("dismissed")
            } label: {
                Label("Dismiss", systemImage: "xmark.circle")
            }
        }
    }

    private var priorityColor: Color {
        switch task.priority {
        case "high": return .red
        case "medium": return .orange
        case "low": return .gray
        default: return .primary
        }
    }
}
