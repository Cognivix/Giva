// ChatView.swift - Message list with streaming text and input field.

import SwiftUI

struct ChatView: View {
    @EnvironmentObject var viewModel: GivaViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Message list
            ScrollViewReader { proxy in
                ScrollView {
                    if viewModel.messages.isEmpty {
                        VStack(spacing: 12) {
                            Image(systemName: "bubble.left.and.text.bubble.right")
                                .font(.system(size: 32))
                                .foregroundColor(.secondary.opacity(0.5))
                            Text("Ask Giva anything about your emails,\ncalendar, or tasks.")
                                .font(.callout)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .padding(.top, 60)
                    } else {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            ForEach(viewModel.messages) { message in
                                MessageBubble(message: message)
                                    .id(message.id)
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
                    .disabled(viewModel.isStreaming || viewModel.isRecording)

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

struct MessageBubble: View {
    let message: ChatMessage
    @State private var showThinking = false

    var body: some View {
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
                    Text(message.content)
                        .font(.system(size: 13))
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
                        if message.isThinking {
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
    }
}

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
