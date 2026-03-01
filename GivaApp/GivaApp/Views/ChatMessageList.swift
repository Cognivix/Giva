// ChatMessageList.swift - Reusable scrollable message list with auto-scroll.
//
// Used by ChatView, TaskChatView, TaskDetailView, and GoalsWindowView.
// Handles: auto-scroll on new messages/content, empty states, loading states,
// agent confirmation cards, agent action bubbles, and standard MessageBubble rendering.

import SwiftUI

// MARK: - Chat Message List

/// Reusable scrollable message list with auto-scroll behavior.
///
/// Renders `ChatMessage` arrays with consistent styling: user bubbles, assistant
/// bubbles (with markdown + thinking panes), agent action badges, and agent
/// confirmation cards. Handles empty and loading states.
struct ChatMessageList: View {
    let messages: [ChatMessage]
    var isLoadingModel: Bool = false
    var isLoading: Bool = false
    var emptyIcon: String = "bubble.left.and.text.bubble.right"
    var emptyTitle: String = "No messages yet"
    var emptySubtitle: String? = nil
    var pendingConfirmation: AgentConfirmation? = nil
    var onApproveAgent: ((String) -> Void)? = nil
    var onDismissAgent: ((String) -> Void)? = nil

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if isLoading {
                    loadingState
                } else if messages.isEmpty {
                    emptyState
                } else {
                    messageList
                }
            }
            .frame(maxHeight: .infinity)
            .onChange(of: messages.count) { _, _ in
                scrollToBottom(proxy)
            }
            .onChange(of: messages.last?.content.count ?? 0) { _, _ in
                scrollToBottom(proxy)
            }
            .onChange(of: messages.last?.thinkingContent.count ?? 0) { _, _ in
                scrollToBottom(proxy)
            }
        }
    }

    // MARK: - Loading State

    private var loadingState: some View {
        VStack {
            ProgressView()
                .controlSize(.small)
            Text("Loading conversation...")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.top, 60)
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: emptyIcon)
                .font(.system(size: 32))
                .foregroundColor(.secondary.opacity(0.5))
            Text(emptyTitle)
                .font(.callout)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            if let subtitle = emptySubtitle {
                Text(subtitle)
                    .font(.caption)
                    .foregroundColor(.secondary.opacity(0.7))
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.top, 60)
    }

    // MARK: - Message List

    private var messageList: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(messages) { message in
                messageView(message)
                    .id(message.id)
            }
        }
        .padding(12)
    }

    // MARK: - Per-Message Rendering

    @ViewBuilder
    private func messageView(_ message: ChatMessage) -> some View {
        if message.role == "system" {
            systemMessageView(message)
        } else {
            MessageBubble(
                message: message,
                isLoadingModel: isLoadingModel
            )
        }
    }

    @ViewBuilder
    private func systemMessageView(_ message: ChatMessage) -> some View {
        if let jobId = agentConfirmJobId(from: message),
           let confirmation = pendingConfirmation,
           confirmation.id == jobId {
            AgentConfirmationCard(
                confirmation: confirmation,
                onApprove: { onApproveAgent?(jobId) },
                onDismiss: { onDismissAgent?(jobId) }
            )
        } else if message.content.hasPrefix("[AGENT_CONFIRM:") {
            // Confirmation already handled — show status badge
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle")
                    .foregroundColor(.green)
                Text("Agent approved")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
        } else {
            // Agent action notification — compact inline style
            AgentActionBubble(content: message.content)
        }
    }

    // MARK: - Scroll

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        if let last = messages.last {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(last.id, anchor: .bottom)
            }
        }
    }
}

// MARK: - Agent Action Bubble

/// Compact inline display for agent action system messages.
private struct AgentActionBubble: View {
    let content: String

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "gearshape.2")
                .font(.system(size: 9))
                .foregroundColor(.secondary)
            Text(content)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
                .italic()
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(Color.secondary.opacity(0.06))
        .cornerRadius(6)
        .frame(maxWidth: .infinity, alignment: .center)
    }
}

// MARK: - Helpers

/// Extract agent job ID from a system message marker like `[AGENT_CONFIRM:uuid]`.
private func agentConfirmJobId(from message: ChatMessage) -> String? {
    guard message.role == "system",
          message.content.hasPrefix("[AGENT_CONFIRM:"),
          message.content.hasSuffix("]")
    else { return nil }

    let start = message.content.index(message.content.startIndex, offsetBy: 15)
    let end = message.content.index(before: message.content.endIndex)
    guard start < end else { return nil }
    return String(message.content[start..<end])
}
