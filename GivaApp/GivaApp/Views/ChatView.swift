// ChatView.swift - Message list with streaming text and input field.
// Assistant messages render Markdown (headers, bold, italic, code, links, lists).

import SwiftUI

struct ChatView: View {
    @Environment(GivaViewModel.self) private var viewModel

    var body: some View {
        @Bindable var viewModel = viewModel

        VStack(spacing: 0) {
            // Message list
            ScrollViewReader { proxy in
                ScrollView {
                    if viewModel.messages.isEmpty {
                        VStack(spacing: 12) {
                            if viewModel.serverPhase == .syncing {
                                ProgressView()
                                    .controlSize(.large)
                                    .padding(.bottom, 4)
                                Text("Syncing your data...\nYou'll be able to chat once setup completes.")
                                    .font(.callout)
                                    .foregroundColor(.secondary)
                                    .multilineTextAlignment(.center)
                            } else {
                                Image(systemName: "bubble.left.and.text.bubble.right")
                                    .font(.system(size: 32))
                                    .foregroundColor(.secondary.opacity(0.5))
                                Text("Ask Giva anything about your emails,\ncalendar, or tasks.")
                                    .font(.callout)
                                    .foregroundColor(.secondary)
                                    .multilineTextAlignment(.center)
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .padding(.top, 60)
                    } else {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(viewModel.messages) { message in
                                if let jobId = agentConfirmJobId(from: message),
                                   let confirmation = viewModel.pendingConfirmation,
                                   confirmation.id == jobId {
                                    // Render inline agent confirmation card
                                    AgentConfirmationCard(
                                        confirmation: confirmation,
                                        onApprove: { viewModel.approveAgent(jobId: jobId) },
                                        onDismiss: { viewModel.dismissAgent(jobId: jobId) }
                                    )
                                    .id(message.id)
                                } else if message.role == "system"
                                            && message.content.hasPrefix("[AGENT_CONFIRM:") {
                                    // Confirmation already handled — show status
                                    HStack(spacing: 6) {
                                        Image(systemName: "checkmark.circle")
                                            .foregroundColor(.green)
                                        Text("Agent approved")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 4)
                                    .id(message.id)
                                } else {
                                    MessageBubble(
                                        message: message,
                                        isLoadingModel: viewModel.isLoadingModel
                                    )
                                    .id(message.id)
                                }
                            }
                        }
                        .padding(12)
                    }
                }
                .onChange(of: viewModel.messages.count) { _, _ in
                    if let last = viewModel.messages.last {
                        withAnimation(.easeOut(duration: 0.2)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
                // Also scroll when streaming content updates
                .onChange(of: viewModel.messages.last?.content.count ?? 0) { _, _ in
                    if let last = viewModel.messages.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
                // Scroll during thinking too
                .onChange(of: viewModel.messages.last?.thinkingContent.count ?? 0) { _, _ in
                    if let last = viewModel.messages.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }

            Divider()

            // Input field
            HStack(spacing: 8) {
                // Voice toggle button
                Button(action: { viewModel.isVoiceEnabled.toggle() }) {
                    Image(systemName: viewModel.isVoiceEnabled ? "speaker.wave.2.fill" : "speaker.slash")
                        .font(.system(size: 14))
                        .foregroundColor(viewModel.isVoiceEnabled ? .accentColor : .secondary)
                }
                .buttonStyle(.plain)
                .help(viewModel.isVoiceEnabled ? "Disable voice responses" : "Enable voice responses")

                TextField(
                    viewModel.isOnboarding ? "Answer..." : "Ask Giva...",
                    text: $viewModel.currentInput
                )
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
                    .onSubmit {
                        viewModel.sendMessage()
                    }
                    .disabled(!viewModel.isChatEnabled || viewModel.isStreaming || viewModel.isRecording)

                if viewModel.isRecording {
                    HStack(spacing: 4) {
                        Circle()
                            .fill(.red)
                            .frame(width: 8, height: 8)
                        Text("Listening...")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                } else if viewModel.isStreaming {
                    Button(action: { viewModel.cancelStreaming() }) {
                        Image(systemName: "stop.circle.fill")
                            .font(.system(size: 18))
                            .foregroundColor(.red)
                    }
                    .buttonStyle(.plain)
                    .help("Stop generating")
                } else {
                    // Mic button
                    Button(action: { viewModel.startVoiceInput() }) {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 16))
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .help("Speak a query")

                    // Send button
                    Button(action: { viewModel.sendMessage() }) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 18))
                            .foregroundColor(.accentColor)
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.currentInput.trimmingCharacters(in: .whitespaces).isEmpty)
                    .help("Send message")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
    }
}

// MARK: - Agent Confirmation Helpers

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

// MARK: - Message Bubble

struct MessageBubble: View {
    let message: ChatMessage
    var isLoadingModel: Bool = false
    @State private var showThinking = false

    @ViewBuilder
    var body: some View {
        // System messages render as subtle inline text
        if message.role == "system" {
            HStack(spacing: 6) {
                Text(message.content)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .italic()
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 2)
        } else {

        HStack(alignment: .top) {
            if message.role == "user" {
                Spacer(minLength: 60)
            }

            VStack(alignment: message.role == "user" ? .trailing : .leading, spacing: 4) {
                // Thinking pane (assistant messages only)
                if message.role != "user" && !message.thinkingContent.isEmpty {
                    ThinkingPane(
                        content: message.thinkingContent,
                        isThinking: message.isThinking,
                        isExpanded: $showThinking
                    )
                }

                // Main content bubble
                if !message.content.isEmpty || message.isStreaming {
                    Group {
                        if message.role == "user" {
                            // User messages: plain text
                            Text(message.content)
                                .font(.system(size: 13))
                        } else {
                            // Assistant messages: Markdown rendering
                            MarkdownText(message.content)
                                .font(.system(size: 13))
                        }
                    }
                    .textSelection(.enabled)
                    .lineLimit(nil)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(
                        message.role == "user"
                            ? Color.accentColor.opacity(0.15)
                            : Color(nsColor: .controlBackgroundColor)
                    )
                    .cornerRadius(10)
                }

                if message.isStreaming {
                    HStack(spacing: 4) {
                        ProgressView()
                            .controlSize(.mini)
                        if isLoadingModel {
                            Text("Loading AI model...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        } else if message.isThinking {
                            Text("Thinking...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        } else {
                            Text("Generating...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }

            if message.role != "user" {
                Spacer(minLength: 40)
            }
        }

        } // end else (non-system)
    }
}

// MARK: - Markdown Text

/// Renders Markdown content using `AttributedString`.
///
/// Handles headers, bold, italic, inline code, code blocks, links, lists,
/// and numbered lists. Falls back to plain text if markdown parsing fails (e.g. mid-stream
/// when tokens arrive incrementally and syntax is incomplete).
struct MarkdownText: View {
    let source: String

    init(_ source: String) {
        self.source = source
    }

    var body: some View {
        if let attributed = Self.parseMarkdown(source) {
            Text(attributed)
        } else {
            Text(source)
        }
    }

    /// Parse markdown string into an `AttributedString`.
    ///
    /// Uses `.full` syntax to support block-level elements (headers, lists,
    /// numbered lists, blockquotes) alongside inline formatting (bold, italic,
    /// inline code, links). Falls back to `.inlineOnlyPreservingWhitespace`
    /// if full parsing fails (e.g. mid-stream incomplete markdown), then to
    /// plain text as a last resort.
    private static func parseMarkdown(_ text: String) -> AttributedString? {
        guard !text.isEmpty else { return nil }

        // Try full markdown parsing first (supports headers, lists, etc.)
        let fullOptions = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .full
        )
        if let result = try? AttributedString(markdown: text, options: fullOptions) {
            return result
        }

        // Fallback: inline-only parsing (more tolerant of incomplete markdown
        // during streaming). Pre-process code blocks since this mode doesn't
        // handle fenced blocks.
        let processed = preprocessCodeBlocks(text)
        let inlineOptions = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        if let result = try? AttributedString(markdown: processed, options: inlineOptions) {
            return result
        }

        return nil
    }

    /// Convert fenced code blocks (```...```) into inline-code-styled blocks.
    ///
    /// Used only in the inline-only fallback path. Wraps each line in backticks
    /// so they render as inline code while preserving the block structure.
    private static func preprocessCodeBlocks(_ text: String) -> String {
        var result: [String] = []
        var inCodeBlock = false
        let lines = text.components(separatedBy: "\n")

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                inCodeBlock.toggle()
                if !inCodeBlock {
                    // Closing fence — skip the line
                    continue
                }
                // Opening fence — skip the line (language hint is decorative)
                continue
            }

            if inCodeBlock {
                // Wrap code lines in backticks for inline code rendering.
                // Escape any existing backticks to avoid broken markdown.
                let escaped = line.replacingOccurrences(of: "`", with: "'")
                // Empty lines in code blocks become a non-breaking space in code
                let codeLine = escaped.isEmpty ? "` `" : "`\(escaped)`"
                result.append(codeLine)
            } else {
                result.append(line)
            }
        }

        return result.joined(separator: "\n")
    }
}

// MARK: - Thinking Pane

struct ThinkingPane: View {
    let content: String
    let isThinking: Bool
    @Binding var isExpanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Toggle header
            Button(action: { withAnimation(.easeInOut(duration: 0.2)) { isExpanded.toggle() } }) {
                HStack(spacing: 4) {
                    Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(.secondary)
                        .frame(width: 12)

                    Image(systemName: "brain")
                        .font(.system(size: 10))
                        .foregroundColor(.purple.opacity(0.8))

                    Text(isThinking ? "Thinking..." : "Thought process")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.secondary)

                    if isThinking {
                        ProgressView()
                            .controlSize(.mini)
                            .scaleEffect(0.7)
                    }

                    Spacer()
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
            }
            .buttonStyle(.plain)

            // Expandable content
            if isExpanded {
                Text(content)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .textSelection(.enabled)
                    .lineLimit(nil)
                    .padding(.horizontal, 8)
                    .padding(.bottom, 6)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background(Color.purple.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.purple.opacity(0.15), lineWidth: 0.5)
        )
        // Auto-expand while actively thinking
        .onChange(of: isThinking) { _, newValue in
            if newValue {
                withAnimation(.easeInOut(duration: 0.2)) { isExpanded = true }
            }
        }
        .onAppear {
            // Start expanded if currently thinking
            if isThinking { isExpanded = true }
        }
    }
}
